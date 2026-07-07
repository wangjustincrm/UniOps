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


def test_build_summary_templates():
    from app.services.journal_voucher import build_summary
    assert build_summary("ap_invoice", "accrual", "AP-1", "ACME") == "应付计提 · AP-1 · ACME"
    assert build_summary("pa", "payment", "PA-9", "ACME") == "付款 · PA-9 · ACME"
    # unknown event falls back to doc number
    assert build_summary("gl_opening", "opening", "OB-1", None) == "OB-1"
