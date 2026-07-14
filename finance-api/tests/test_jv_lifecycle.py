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


async def test_review_batch_reports_per_id(db_session):
    good = await _draft_jv(db_session, uuid.uuid4())
    already = await _draft_jv(db_session, uuid.uuid4())
    await jv_crud.review(db_session, already.id, _user())  # already reviewed → error in batch
    missing = uuid.uuid4()

    res = await jv_crud.review_batch(db_session, [good.id, already.id, missing], _user())
    by_id = {r["id"]: r for r in res}
    assert by_id[str(good.id)]["ok"] is True
    assert by_id[str(already.id)]["ok"] is False
    assert by_id[str(missing)]["ok"] is False
    assert (await jv_crud.get(db_session, good.id)).status == "reviewed"


async def test_post_batch_posts_reviewed_only(db_session):
    a = await _draft_jv(db_session, uuid.uuid4())
    b = await _draft_jv(db_session, uuid.uuid4())
    await _open_period(db_session, a.fiscal_period)
    await jv_crud.review(db_session, a.id, _user())
    # b left as draft → should fail in batch
    res = await jv_crud.post_batch(db_session, [a.id, b.id], _user())
    by_id = {r["id"]: r for r in res}
    assert by_id[str(a.id)]["ok"] is True
    assert by_id[str(b.id)]["ok"] is False
    assert (await jv_crud.get(db_session, a.id)).status == "posted"


async def test_gl_opening_and_close_jvs_post_immediately(db_session):
    """spec §4: 开账/结转 are system-authoritative — their JVs skip the human
    review/post flow and land already posted."""
    from datetime import date
    from app.crud.gl import post_opening_balance
    from app.models.journal_voucher import JournalVoucher

    r = await post_opening_balance(db_session, as_of=date(2026, 1, 1), lines=[
        {"account_code": "1010", "debit": "100.00"},
        {"account_code": "3100", "credit": "100.00"},
    ])
    assert r["posting_event_id"] is not None
    jv = (await db_session.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == r["posting_event_id"]))).scalar_one()
    assert jv.status == "posted"
    assert jv.posted_at is not None


async def test_reverse_copies_promoted_income_expense_item(db_session):
    """红冲 red lines must carry income_expense_item_id, else item-level
    expansions stop netting (red lands in the (none) group)."""
    from app.services.posting import emit_event

    ba_id = uuid.uuid4()
    # Create a posted JV with income_expense_item promoted to column
    ev_id = await emit_event(
        db_session, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-5", event_type="accrual",
        prepared_by=uuid.uuid4(),
        lines=[
            {"line_role": "purchase_expense", "account_code": "5101",
             "debit": Decimal("50.00"), "currency": "CAD",
             "aux": {"income_expense_item": {"value_id": ba_id, "value_text": "CRM005"}}},
            {"line_role": "accounts_payable", "account_code": "2000",
             "credit": Decimal("50.00"), "currency": "CAD"},
        ],
    )
    jv = (await db_session.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == ev_id))).scalar_one()

    # Review and post
    await _open_period(db_session, jv.fiscal_period)
    await jv_crud.review(db_session, jv.id, _user())
    await jv_crud.post(db_session, jv.id, _user())

    # Verify original line has income_expense_item_id set
    orig_line = (await db_session.execute(
        select(JournalVoucherLine).where(
            JournalVoucherLine.jv_id == jv.id,
            JournalVoucherLine.account_code == "5101"
        )
    )).scalar_one()
    assert orig_line.income_expense_item_id == ba_id

    # Reverse
    actor = _user()
    red = await jv_crud.reverse(db_session, jv.id, actor)

    # Fetch red line and verify income_expense_item_id is carried over
    red_line = (await db_session.execute(
        select(JournalVoucherLine).where(
            JournalVoucherLine.jv_id == red.id,
            JournalVoucherLine.account_code == "5101"
        )
    )).scalar_one()
    assert red_line.income_expense_item_id == ba_id
    assert red_line.orig_debit == Decimal("-50.00")
