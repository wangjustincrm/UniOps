"""POST /expenses/{id}/remind — the Approval Status card's "Send reminder".

The send itself happens in epms-api (the only service with the notification
machinery); expense-api's job is the claim's read gate and the status gate,
and passing epms-api's user-facing refusals through unchanged.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.expense import ExpenseClaim
from app.services import epms_reminder_client
from tests.conftest import _client, _make_token


async def _claim(test_engine, *, owner: uuid.UUID, status: str = "submitted") -> uuid.UUID:
    cid = uuid.uuid4()
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        s.add(ExpenseClaim(
            id=cid, claim_number=f"EC-REM-{cid.hex[:8]}", claim_type="EXP",
            employee_id=owner, employee_name="Claimant",
            department_name="IT", submission_date=date(2026, 10, 6),
            status=status, approval_step_idx=0, created_by=owner,
            total_amount=Decimal("100.00"),
        ))
        await s.commit()
    return cid


@pytest.fixture
def epms_calls(monkeypatch):
    calls: list[tuple] = []

    async def _fake(claim_id, claim_type, bearer_token):
        calls.append((claim_id, claim_type, bearer_token))
        return {"sent": True, "document_number": "EC-REM", "recipients": ["Dept Manager"],
                "next_allowed_at": "2026-10-09T12:00:00+00:00"}

    monkeypatch.setattr(epms_reminder_client, "send_expense_reminder", _fake)
    return calls


@pytest.mark.asyncio
async def test_claimant_can_send_a_reminder(test_engine, epms_calls):
    owner = uuid.uuid4()
    cid = await _claim(test_engine, owner=owner)
    token = _make_token("requester", str(owner))
    async with _client(token) as c:
        r = await c.post(f"/api/v1/expenses/{cid}/remind", json={})
    assert r.status_code == 200, r.text
    assert r.json()["recipients"] == ["Dept Manager"]
    # Forwarded with the claim's type and the caller's own bearer.
    assert epms_calls == [(cid, "EXP", token)]


@pytest.mark.asyncio
async def test_unrelated_user_cannot_send_a_reminder(test_engine, epms_calls):
    cid = await _claim(test_engine, owner=uuid.uuid4())
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.post(f"/api/v1/expenses/{cid}/remind", json={})
    assert r.status_code == 403
    assert epms_calls == []


@pytest.mark.parametrize("status", ["draft", "returned", "approved", "rejected", "paid"])
@pytest.mark.asyncio
async def test_no_reminder_unless_the_claim_is_with_an_approver(test_engine, epms_calls, status):
    owner = uuid.uuid4()
    cid = await _claim(test_engine, owner=owner, status=status)
    async with _client(_make_token("requester", str(owner))) as c:
        r = await c.post(f"/api/v1/expenses/{cid}/remind", json={})
    assert r.status_code == 409
    assert epms_calls == []


@pytest.mark.asyncio
async def test_epms_refusal_is_passed_through_verbatim(test_engine, monkeypatch):
    async def _cooldown(*_a, **_k):
        raise epms_reminder_client.ReminderRefused(429, "A reminder was already sent for this step.")

    monkeypatch.setattr(epms_reminder_client, "send_expense_reminder", _cooldown)
    owner = uuid.uuid4()
    cid = await _claim(test_engine, owner=owner, status="in_review")
    async with _client(_make_token("requester", str(owner))) as c:
        r = await c.post(f"/api/v1/expenses/{cid}/remind", json={})
    assert r.status_code == 429
    assert r.json()["detail"] == "A reminder was already sent for this step."


@pytest.mark.asyncio
async def test_missing_claim_is_404(test_engine, epms_calls):
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.post(f"/api/v1/expenses/{uuid.uuid4()}/remind", json={})
    assert r.status_code == 404
