"""JV subsystem — data model + generation (Plan 1)."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.journal_voucher import JournalVoucher, JournalVoucherLine, JvLineDimension


async def test_can_insert_jv_with_lines_and_dims(db_session):
    jv = JournalVoucher(
        jv_number="JV-202607-0001", voucher_word="JV",
        voucher_date=date(2026, 7, 1), fiscal_period="2026-07",
        summary="test", status="draft",
        total_debit=Decimal("100.00"), total_credit=Decimal("100.00"),
        total_local_debit=Decimal("100.00"), total_local_credit=Decimal("100.00"),
    )
    db_session.add(jv)
    await db_session.flush()
    line = JournalVoucherLine(
        jv_id=jv.id, line_no=1, account_code="5000",
        orig_debit=Decimal("100.00"), local_debit=Decimal("100.00"),
        currency="CAD", fx_rate=Decimal("1"),
    )
    db_session.add(line)
    await db_session.flush()
    db_session.add(JvLineDimension(jv_line_id=line.id, dim_code="cost_center",
                                   value_text="CC-1"))
    await db_session.flush()

    got = (await db_session.execute(
        select(JournalVoucher).where(JournalVoucher.id == jv.id))).scalar_one()
    assert got.status == "draft"
    assert got.jv_number == "JV-202607-0001"


async def test_next_jv_number_increments_per_period(db_session):
    from app.services.journal_voucher import next_jv_number
    n1 = await next_jv_number(db_session, "2026-07")
    assert n1 == "JV-202607-0001"
    db_session.add(JournalVoucher(
        jv_number=n1, voucher_word="JV", voucher_date=date(2026, 7, 1),
        fiscal_period="2026-07", status="draft"))
    await db_session.flush()
    n2 = await next_jv_number(db_session, "2026-07")
    assert n2 == "JV-202607-0002"
    # different period resets
    assert await next_jv_number(db_session, "2026-08") == "JV-202608-0001"


async def test_next_jv_number_is_max_based_not_count(db_session):
    """NC-imported history shares the JV- namespace and can have numbering gaps
    (deleted NC vouchers): with 1 row numbered 0007, a count would hand out
    0002 forever colliding at 0007 — max-based must return 0008."""
    from app.services.journal_voucher import next_jv_number
    db_session.add(JournalVoucher(
        jv_number="JV-202609-0007", voucher_word="JV", voucher_date=date(2026, 9, 1),
        fiscal_period="2026-09", status="posted", nc_source_pk="NCGAP1"))
    await db_session.flush()
    assert await next_jv_number(db_session, "2026-09") == "JV-202609-0008"


def test_build_summary_templates():
    from app.services.journal_voucher import build_summary
    assert build_summary("ap_invoice", "accrual", "AP-1", "ACME") == "应付计提 · AP-1 · ACME"
    assert build_summary("pa", "payment", "PA-9", "ACME") == "付款 · PA-9 · ACME"
    # unknown event falls back to doc number
    assert build_summary("gl_opening", "opening", "OB-1", None) == "OB-1"


async def _make_event(db, *, currency="CAD", fx="1", debit="100.00", credit="0",
                      prepared_by=None):
    from app.services.posting import emit_event
    ev_id = await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        prepared_by=prepared_by,
        lines=[
            {"line_role": "purchase_expense", "account_code": "5000",
             "debit": Decimal(debit), "currency": currency, "fx_rate": Decimal(fx),
             "partner_name": "ACME"},
            {"line_role": "accounts_payable", "account_code": "2000",
             "credit": Decimal("100.00"), "currency": currency, "fx_rate": Decimal(fx),
             "partner_name": "ACME"},
        ],
    )
    return ev_id


async def test_generate_from_event_creates_balanced_draft_jv(db_session):
    from app.services.journal_voucher import generate_from_event
    prepared = uuid.uuid4()
    ev_id = await _make_event(db_session, prepared_by=prepared)
    jv = await generate_from_event(db_session, ev_id, prepared)
    assert jv is not None
    assert jv.status == "draft"
    assert jv.jv_number.startswith("JV-")
    assert jv.prepared_by == prepared
    assert jv.summary == "应付计提 · AP-1 · ACME"
    assert jv.total_debit == Decimal("100.00")
    assert jv.total_credit == Decimal("100.00")
    lines = (await db_session.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id == jv.id)
        .order_by(JournalVoucherLine.line_no))).scalars().all()
    assert len(lines) == 2
    assert lines[0].account_code == "5000"
    assert lines[0].orig_debit == Decimal("100.00")
    assert lines[0].local_debit == Decimal("100.00")   # CAD, fx 1


async def test_generate_computes_local_from_fx(db_session):
    from app.services.journal_voucher import generate_from_event
    ev_id = await _make_event(db_session, currency="USD", fx="1.35", debit="100.00")
    jv = await generate_from_event(db_session, ev_id, uuid.uuid4())
    line = (await db_session.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id == jv.id,
                                          JournalVoucherLine.orig_debit > 0))).scalar_one()
    assert line.currency == "USD"
    assert line.orig_debit == Decimal("100.00")
    assert line.local_debit == Decimal("135.00")       # 100 * 1.35
    assert jv.total_local_debit == Decimal("135.00")


async def test_generate_is_idempotent_per_event(db_session):
    from app.services.journal_voucher import generate_from_event
    ev_id = await _make_event(db_session)
    jv1 = await generate_from_event(db_session, ev_id, uuid.uuid4())
    jv2 = await generate_from_event(db_session, ev_id, uuid.uuid4())
    assert jv2.id == jv1.id                              # returns existing, no dup
    all_jv = (await db_session.execute(
        select(JournalVoucher).where(JournalVoucher.posting_event_id == ev_id))).scalars().all()
    assert len(all_jv) == 1


async def test_emit_event_auto_generates_jv(db_session):
    from app.services.posting import emit_event
    ev_id = await emit_event(
        db_session, source_service="finance", source_doc_type="pa",
        source_doc_id=uuid.uuid4(), source_doc_number="PA-9", event_type="payment",
        prepared_by=uuid.uuid4(),
        lines=[
            {"line_role": "accounts_payable", "account_code": "2000",
             "debit": Decimal("50.00"), "currency": "CAD"},
            {"line_role": "bank", "account_code": "1000",
             "credit": Decimal("50.00"), "currency": "CAD"},
        ],
    )
    jv = (await db_session.execute(
        select(JournalVoucher).where(JournalVoucher.posting_event_id == ev_id))).scalar_one()
    assert jv.status == "draft"
    assert jv.summary == "付款 · PA-9"  # no partner_name on these lines
    assert jv.total_debit == Decimal("50.00")


async def test_emit_event_idempotent_skip_makes_no_jv(db_session):
    from app.services.posting import emit_event
    doc_id = uuid.uuid4()
    kw = dict(source_service="finance", source_doc_type="pa", source_doc_id=doc_id,
              source_doc_number="PA-1", event_type="payment",
              lines=[{"line_role": "bank", "credit": Decimal("1.00"), "currency": "CAD"},
                     {"line_role": "accounts_payable", "debit": Decimal("1.00"), "currency": "CAD"}])
    await emit_event(db_session, **kw)
    second = await emit_event(db_session, **kw)   # ON CONFLICT DO NOTHING → None
    assert second is None
    jvs = (await db_session.execute(select(JournalVoucher).where(
        JournalVoucher.source_doc_id == doc_id))).scalars().all()
    assert len(jvs) == 1
