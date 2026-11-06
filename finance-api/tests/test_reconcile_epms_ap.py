"""scripts.reconcile_epms_ap — Finance AP back in step with EPMS invoices.

The production shape (2026-10-21): Data Maintenance deleted EPMS invoices
without telling Finance, leaving AP rows (some posted, accrued) for invoices
that no longer exist; the re-uploaded real invoice's own sync was then
refused on vendor + invoice number, so it had no AP row — and if it was
paid, its payment writeback found nothing to settle.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.crud import ap_invoice as ap_crud
from app.crud.ap_accrual import post_invoice_accrual
from app.models.ap_invoice import ApInvoice
from app.models.mirrors import Invoice
from app.models.pa import PaymentApplication
from app.models.posting import PostingEvent
from scripts.reconcile_epms_ap import reconcile

VENDOR = uuid.uuid4()


def _payload(number, ref, status="posted", total="113.00"):
    return dict(source_ref=ref, vendor_id=VENDOR, vendor_name="Waste Co",
                vendor_invoice_number=number, amount="100.00", tax_amount="13.00",
                total_amount=total, currency="CAD", invoice_date=date(2026, 10, 1),
                due_date=date(2026, 10, 31), status=status, source_status="matched",
                po_id=None, po_number=None)


async def _orphan(db, number, ref, status="posted"):
    """An AP row for an EPMS invoice that was deleted (no invoices row)."""
    ap = await ap_crud.upsert(db, source="epms", source_invoice_id=uuid.uuid4(),
                              payload=_payload(number, ref, status), tax_lines=[])
    if status == "posted":
        await post_invoice_accrual(db, ap.id)
    await db.flush()
    return ap


async def _live(db, number, ref, status="matched", total="113.00"):
    inv = Invoice(id=uuid.uuid4(), internal_ref=ref, vendor_invoice_number=number,
                  vendor_id=VENDOR, vendor_name="Waste Co", amount=Decimal("100.00"),
                  tax_amount=Decimal(total) - Decimal("100.00"), total_amount=Decimal(total),
                  currency="CAD", invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                  status=status)
    db.add(inv)
    await db.flush()
    return inv


async def _ap_for(db, invoice_id):
    return (await db.execute(select(ApInvoice).where(
        ApInvoice.source == "epms", ApInvoice.source_invoice_id == invoice_id))).scalar_one_or_none()


async def _events(db, ap_id, event_type):
    return (await db.execute(select(PostingEvent).where(
        PostingEvent.source_doc_type == "ap_invoice", PostingEvent.source_doc_id == ap_id,
        PostingEvent.event_type == event_type))).scalars().all()


@pytest.mark.anyio
async def test_the_0653_shape_orphan_voided_and_live_invoice_posted(db_session):
    """Deleted invoice's posted AP row → void + accrual reversed; the
    re-uploaded invoice with the same number → its own posted, accrued row."""
    orphan = await _orphan(db_session, "0743773-0256-2", "INV-2026-0653")
    live = await _live(db_session, "0743773-0256-2", "INV-2026-0653")

    report = await reconcile(db_session)

    assert [(k, o) for k, _, o in report] == [("orphan", "voided"),
                                              ("missing", "created posted + accrual")]
    await db_session.refresh(orphan)
    assert orphan.status == "void"
    assert len(await _events(db_session, orphan.id, "accrual_reversal")) == 1
    ap = await _ap_for(db_session, live.id)
    assert ap.status == "posted" and ap.total_amount == Decimal("113.00")
    assert ap.source_ref == "INV-2026-0653"
    assert len(await _events(db_session, ap.id, "accrual")) == 1


@pytest.mark.anyio
async def test_a_never_posted_orphan_is_removed(db_session):
    """INV-2026-0651's shape: a draft AP row, nothing in the GL."""
    orphan = await _orphan(db_session, "13660", "INV-2026-0651", status="draft")
    report = await reconcile(db_session)
    assert report == [("orphan", report[0][1], "deleted")]
    assert (await db_session.execute(
        select(ApInvoice).where(ApInvoice.id == orphan.id))).scalar_one_or_none() is None


@pytest.mark.anyio
async def test_a_paid_invoice_is_rebuilt_and_settled_by_its_pa(db_session):
    """INV-2026-0654 / 0500 shape: paid in EPMS by a processed PA, no AP row.
    Rebuilt as at match (posted + accrual), then the PA's writeback settles it."""
    live = await _live(db_session, "58754243", "INV-2026-0500", status="paid")
    db_session.add(PaymentApplication(
        pa_number="PA-20260918-0003", title="t", pa_type="regular", status="processed",
        vendor_id=VENDOR, vendor_name="Waste Co", po_id=uuid.uuid4(), payment_amount=113,
        currency="CAD", invoice_ids=[str(live.id)], created_by=uuid.uuid4()))
    await db_session.flush()

    report = await reconcile(db_session)

    assert report[0][2] == "created posted + accrual, payment replayed from PA-20260918-0003 → paid"
    ap = await _ap_for(db_session, live.id)
    assert ap.status == "paid" and ap.paid_amount == ap.total_amount
    assert len(await _events(db_session, ap.id, "accrual")) == 1


@pytest.mark.anyio
async def test_paid_without_a_paid_pa_is_flagged_not_guessed(db_session):
    live = await _live(db_session, "X-1", "INV-2026-0700", status="paid")
    report = await reconcile(db_session)
    assert "NO paid PA found" in report[0][2]
    assert (await _ap_for(db_session, live.id)).status == "posted"


@pytest.mark.anyio
async def test_an_unmatched_invoice_is_rebuilt_as_draft_without_accrual(db_session):
    live = await _live(db_session, "U-1", "INV-2026-0701", status="unmatched")
    report = await reconcile(db_session)
    assert report[0][2] == "created draft"
    ap = await _ap_for(db_session, live.id)
    assert ap.status == "draft" and await _events(db_session, ap.id, "accrual") == []


@pytest.mark.anyio
async def test_a_genuine_number_clash_is_skipped_and_reported(db_session):
    """WTC 000010 shape: two live invoices share one number; Finance keeps one
    AP row per vendor + number, so the second needs a person."""
    first = await _live(db_session, "000010", "INV-2026-0447")
    await ap_crud.upsert(db_session, source="epms", source_invoice_id=first.id,
                         payload=_payload("000010", "INV-2026-0447"), tax_lines=[])
    second = await _live(db_session, "000010", "INV-2026-0603")
    report = await reconcile(db_session)
    assert report == [("missing", report[0][1], "SKIPPED — vendor + invoice # already on another AP row")]
    assert await _ap_for(db_session, second.id) is None
    assert (await _ap_for(db_session, first.id)) is not None      # the savepoint kept the rest


@pytest.mark.anyio
async def test_pms_migration_rows_are_not_missing(db_session):
    await _live(db_session, "OLD-1", "INV-6727", status="paid")
    assert await reconcile(db_session) == []


@pytest.mark.anyio
async def test_a_second_run_finds_nothing(db_session):
    await _orphan(db_session, "N-1", "INV-2026-0001")
    await _live(db_session, "N-1", "INV-2026-0002")
    assert len(await reconcile(db_session)) == 2
    assert await reconcile(db_session) == []
