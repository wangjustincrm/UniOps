"""JV lifecycle crud — Plan 2."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.crud import journal_voucher as jv_crud
from app.models.journal_voucher import JournalVoucher, JournalVoucherLine
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


async def _open_period(db, period="2026-07"):
    # A row with status != OPEN would block posting; absence of a row means open.
    # Insert an explicit OPEN row to be deterministic regardless of today's date.
    from app.models.fiscal_period import OPEN, FiscalPeriod
    existing = (await db.execute(select(FiscalPeriod).where(FiscalPeriod.period == period))).scalar_one_or_none()
    if existing is None:
        db.add(FiscalPeriod(period=period, status=OPEN))
        await db.flush()


async def test_post_moves_reviewed_to_posted(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    await _open_period(db_session, jv.fiscal_period)
    await jv_crud.review(db_session, jv.id, _user())
    out = await jv_crud.post(db_session, jv.id, _user())
    assert out.status == "posted"
    assert out.posted_at is not None
    assert out.posted_by is not None


async def test_post_rejects_non_reviewed(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    with pytest.raises(jv_crud.JvStateError):
        await jv_crud.post(db_session, jv.id, _user())  # still draft


async def test_post_blocked_in_closed_period(db_session):
    from app.models.fiscal_period import FiscalPeriod
    jv = await _draft_jv(db_session, uuid.uuid4())
    await jv_crud.review(db_session, jv.id, _user())
    db_session.add(FiscalPeriod(period=jv.fiscal_period, status="hard_closed"))
    await db_session.flush()
    with pytest.raises(jv_crud.JvStateError):
        await jv_crud.post(db_session, jv.id, _user())


async def test_unpost_moves_posted_to_reviewed(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    await _open_period(db_session, jv.fiscal_period)
    await jv_crud.review(db_session, jv.id, _user())
    await jv_crud.post(db_session, jv.id, _user())
    out = await jv_crud.unpost(db_session, jv.id, _user())
    assert out.status == "reviewed"
    assert out.posted_by is None and out.posted_at is None


async def test_reverse_creates_red_voucher_and_marks_original(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    await _open_period(db_session, jv.fiscal_period)
    await jv_crud.review(db_session, jv.id, _user())
    await jv_crud.post(db_session, jv.id, _user())

    actor = _user()
    red = await jv_crud.reverse(db_session, jv.id, actor)

    # red voucher: posted, links to original, negated amounts
    assert red.id != jv.id
    assert red.status == "posted"
    assert red.reverses_jv_id == jv.id
    assert red.total_debit == Decimal("-100.00")
    assert red.total_credit == Decimal("-100.00")
    red_lines = (await db_session.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id == red.id)
        .order_by(JournalVoucherLine.line_no))).scalars().all()
    assert len(red_lines) == 2
    assert red_lines[0].orig_debit == Decimal("-100.00")
    assert red_lines[0].local_debit == Decimal("-100.00")
    assert red_lines[0].account_code == "5000"

    # original marked reversed + back-link
    original = await jv_crud.get(db_session, jv.id)
    assert original.status == "reversed"
    assert original.reversed_by_jv_id == red.id


async def test_reverse_rejects_non_posted(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    with pytest.raises(jv_crud.JvStateError):
        await jv_crud.reverse(db_session, jv.id, _user())  # draft, not posted
