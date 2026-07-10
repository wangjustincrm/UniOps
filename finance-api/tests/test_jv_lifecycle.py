"""JV lifecycle crud — Plan 2."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.crud import journal_voucher as jv_crud
from app.models.journal_voucher import JournalVoucher
from app.models.mirrors import SodRule
from app.services.posting import emit_event


def _user(sub=None, role="finance_manager"):
    return {"sub": str(sub or uuid.uuid4()), "role": role}


async def _draft_jv(db, prepared_by):
    """Create a draft JV via the Plan 1 generation path."""
    ev_id = await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        prepared_by=prepared_by,
        lines=[
            {"line_role": "purchase_expense", "account_code": "5000",
             "debit": Decimal("100.00"), "currency": "CAD"},
            {"line_role": "accounts_payable", "account_code": "2000",
             "credit": Decimal("100.00"), "currency": "CAD"},
        ],
    )
    return (await db.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == ev_id))).scalar_one()


async def test_review_moves_draft_to_reviewed(db_session):
    preparer = uuid.uuid4()
    jv = await _draft_jv(db_session, preparer)
    reviewer = _user()  # different person
    out = await jv_crud.review(db_session, jv.id, reviewer)
    assert out.status == "reviewed"
    assert str(out.reviewed_by) == reviewer["sub"]
    assert out.reviewed_at is not None


async def test_review_blocks_self_review_when_sod_enabled(db_session):
    preparer = uuid.uuid4()
    jv = await _draft_jv(db_session, preparer)
    db_session.add(SodRule(rule_code="jv_self_review", name="jv self review", enabled=True))
    await db_session.flush()
    with pytest.raises(jv_crud.JvPermissionError):
        await jv_crud.review(db_session, jv.id, _user(sub=preparer))  # same person


async def test_review_requires_finance_role(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    with pytest.raises(jv_crud.JvPermissionError):
        await jv_crud.review(db_session, jv.id, _user(role="requester"))


async def test_review_rejects_non_draft(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    await jv_crud.review(db_session, jv.id, _user())
    with pytest.raises(jv_crud.JvStateError):
        await jv_crud.review(db_session, jv.id, _user())  # already reviewed


async def test_unreview_moves_reviewed_to_draft(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    await jv_crud.review(db_session, jv.id, _user())
    out = await jv_crud.unreview(db_session, jv.id, _user())
    assert out.status == "draft"
    assert out.reviewed_by is None
    assert out.reviewed_at is None
