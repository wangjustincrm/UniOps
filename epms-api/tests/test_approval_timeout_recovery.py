"""delegate_action after the approval engine times out (2026-11-17).

An agreement submit was committed by approval-api — status, event, approver
task — but its reply never arrived; epms-api answered 500 and the page said
"saved as a draft but not submitted". After a timeout the client now looks for
the engine's approval_events row instead of guessing.
"""
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.approval import ApprovalEvent
from app.services import approval_client
from tests.test_agreement_invoice_match import _make_active_agreement
from tests.test_agreements import seed_vendor_and_user

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def fast_settle(monkeypatch):
    monkeypatch.setattr(approval_client, "SETTLE_WAIT_S", 1.0)
    monkeypatch.setattr(approval_client, "SETTLE_POLL_S", 0.1)


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _submitted_agreement(test_engine):
    vendor_id, _, user_id = await seed_vendor_and_user(
        test_engine, vendor_name=f"Timeout V {uuid.uuid4().hex[:6]}")
    agr = await _make_active_agreement(test_engine, vendor_id, user_id, status="draft")
    return agr, uuid.UUID(str(user_id))


async def _engine_applies(test_engine, agr, actor_id, *, action="submit", backdate=False):
    """What approval-api does: move the document and record the event."""
    async with _factory(test_engine)() as db:
        await db.execute(text(
            "UPDATE purchase_agreements SET status='submitted' WHERE id=:i"), {"i": agr.id})
        ev = ApprovalEvent(document_type="agr", document_id=agr.id, document_number=agr.number,
                           step_idx=0, action=action, actor_id=actor_id, actor_role="system_admin")
        db.add(ev)
        await db.flush()
        if backdate:
            await db.execute(text(
                "UPDATE approval_events SET created_at = now() - interval '1 hour' WHERE id=:i"),
                {"i": ev.id})
        await db.commit()


def _timing_out_post(on_call=None):
    async def _post(self, url, **kw):
        if on_call is not None:
            await on_call()
        raise httpx.ReadTimeout("timed out")
    return _post


async def test_timeout_but_applied_returns_the_real_status(test_engine, monkeypatch):
    agr, actor = await _submitted_agreement(test_engine)
    monkeypatch.setattr(httpx.AsyncClient, "post",
                        _timing_out_post(lambda: _engine_applies(test_engine, agr, actor)))
    result = await approval_client.delegate_action("agr", str(agr.id), "submit", None, "tok")
    assert result["new_status"] == "submitted"
    assert result["recovered_after_timeout"] is True


async def test_timeout_and_not_applied_says_so(test_engine, monkeypatch):
    agr, _ = await _submitted_agreement(test_engine)
    monkeypatch.setattr(httpx.AsyncClient, "post", _timing_out_post())
    with pytest.raises(RuntimeError, match="did not answer in time"):
        await approval_client.delegate_action("agr", str(agr.id), "submit", None, "tok")


async def test_an_older_event_is_not_this_call(test_engine, monkeypatch):
    """A submit from an earlier round (e.g. before a return) must not be
    mistaken for this one."""
    agr, actor = await _submitted_agreement(test_engine)
    await _engine_applies(test_engine, agr, actor, backdate=True)
    monkeypatch.setattr(httpx.AsyncClient, "post", _timing_out_post())
    with pytest.raises(RuntimeError, match="did not answer in time"):
        await approval_client.delegate_action("agr", str(agr.id), "submit", None, "tok")


async def test_a_different_action_is_not_this_call(test_engine, monkeypatch):
    agr, actor = await _submitted_agreement(test_engine)
    monkeypatch.setattr(httpx.AsyncClient, "post", _timing_out_post(
        lambda: _engine_applies(test_engine, agr, actor, action="cancel")))
    with pytest.raises(RuntimeError, match="did not answer in time"):
        await approval_client.delegate_action("agr", str(agr.id), "submit", None, "tok")
