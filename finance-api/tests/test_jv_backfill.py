"""JV backfill — Plan 3 Task 1."""
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.crud import journal_voucher as jv_crud
from app.models.journal_voucher import JournalVoucher
from app.services.posting import emit_event


async def _event(db, prepared=True):
    return await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        prepared_by=uuid.uuid4() if prepared else None,
        lines=[{"line_role": "purchase_expense", "account_code": "5000",
                "debit": Decimal("100.00"), "currency": "CAD"},
               {"line_role": "accounts_payable", "account_code": "2000",
                "credit": Decimal("100.00"), "currency": "CAD"}])


async def test_backfill_posts_existing_draft_jvs(db_session):
    await _event(db_session)
    await _event(db_session)
    res = await jv_crud.backfill_posted_jvs(db_session)
    assert res["posted"] == 2
    posted = (await db_session.execute(
        select(JournalVoucher).where(JournalVoucher.status == "posted"))).scalars().all()
    assert len(posted) == 2


async def test_backfill_generates_jv_for_event_without_one(db_session):
    ev_id = await _event(db_session)
    # simulate a pre-Plan-1 event: delete its auto-generated JV
    await db_session.execute(text("delete from journal_vouchers where posting_event_id = :e"),
                             {"e": str(ev_id)})
    await db_session.flush()
    res = await jv_crud.backfill_posted_jvs(db_session)
    assert res["generated"] == 1
    assert res["posted"] == 1
    jv = (await db_session.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == ev_id))).scalar_one()
    assert jv.status == "posted"


async def test_backfill_is_idempotent(db_session):
    await _event(db_session)
    await jv_crud.backfill_posted_jvs(db_session)
    res2 = await jv_crud.backfill_posted_jvs(db_session)
    assert res2["generated"] == 0 and res2["posted"] == 0
    posted = (await db_session.execute(
        select(JournalVoucher).where(JournalVoucher.status == "posted"))).scalars().all()
    assert len(posted) == 1
