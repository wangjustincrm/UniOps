"""Matching an invoice picks up the GRs that already exist for the same lines.

The GR→invoice direction (gr.create → _autofill_gr_to_matched_invoices) only
covers "invoice matched first, goods received later". The opposite order —
received first, invoiced later — is at least as common, and the match panel
never sends gr_ids, so those invoices used to end up with gr_id NULL until
someone opened the invoice and edited the GR link by hand.

match() therefore auto-discovers GRs when, and only when, the caller made no GR
selection of its own (auto_link_grs=True, set by the match endpoint). Explicit
selections — including an explicit "no GRs" from the edit form, replayed through
rematch_from_existing — are always honoured verbatim.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

import app.db.session as sm
from app.crud import invoice as invoice_crud
from app.crud import user as user_crud
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.invoice import Invoice
from app.models.po import PoLineItem, PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.schemas.invoice import AllocationInput, InvoiceMatchRequest


async def _fixture(db, *, subtotal="50"):
    user = await user_crud.create(db, RegisterRequest(
        email=f"m-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="AP Tester", role="ap_clerk",
    ))
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme",
                    category="supplier", contact_name="C", contact_email="c@x.com")
    db.add(vendor)
    await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=2,
                       vendor_id=vendor.id, vendor_name="Acme", status="issued",
                       subtotal=Decimal(subtotal), created_by=user.id)
    db.add(po)
    await db.flush()
    return user, vendor, po


def _po_line(po_id, desc, unit_price, line_total):
    return PoLineItem(po_id=po_id, description=desc, qty=Decimal("10"), unit="ea",
                      unit_price=Decimal(unit_price), line_total=Decimal(line_total))


def _invoice(vendor_id, uploaded_by, *, amount, ref=None):
    ref = ref or f"INV-{uuid.uuid4().hex[:8]}"
    return Invoice(
        internal_ref=ref, vendor_invoice_number=ref, vendor_id=vendor_id,
        vendor_name="Acme", amount=Decimal(amount), tax_amount=Decimal("0"),
        total_amount=Decimal(amount), invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1), status="unmatched", line_items=[],
        uploaded_by=uploaded_by,
    )


async def _gr_with_line(db, po, vendor_id, created_by, po_line_id, line_total, *, status="pending_ack"):
    gr = GoodsReceipt(
        number=f"GR-{uuid.uuid4().hex[:8]}", title="Goods", po_id=po.id,
        po_number=po.number, vendor_id=vendor_id, vendor_name="Acme",
        gr_type="physical", procurement_type=2, status=status, created_by=created_by,
    )
    db.add(gr)
    await db.flush()
    db.add(GrLineItem(
        gr_id=gr.id, po_line_id=po_line_id, description="widget",
        qty_ordered=Decimal("10"), qty_received=Decimal("10"), unit="ea",
        unit_price=Decimal("5"), line_total=Decimal(line_total),
    ))
    await db.flush()
    return gr


@pytest.mark.asyncio
async def test_match_links_the_gr_that_already_received_those_lines():
    async with sm.AsyncSessionLocal() as db:
        user, vendor, po = await _fixture(db, subtotal="130")
        line1 = _po_line(po.id, "A", "5", "50")
        line2 = _po_line(po.id, "B", "8", "80")
        db.add_all([line1, line2])
        await db.flush()

        gr1 = await _gr_with_line(db, po, vendor.id, user.id, line1.id, "50")
        gr2 = await _gr_with_line(db, po, vendor.id, user.id, line2.id, "80")

        inv = _invoice(vendor.id, user.id, amount="50")
        db.add(inv)
        await db.flush()

        req = InvoiceMatchRequest(allocations=[AllocationInput(
            invoice_line_id=uuid.uuid4(), po_id=po.id, po_line_id=line1.id,
            allocated_amount=Decimal("50"),
        )])
        await invoice_crud.match(db, inv, req, matched_by=user.id, auto_link_grs=True)
        await db.refresh(inv)

        # Only the GR that received line1 — gr2 received a line this invoice
        # does not bill.
        assert inv.gr_ids == [str(gr1.id)]
        assert inv.gr_id == gr1.id
        assert inv.gr_value == Decimal("50.00")
        assert str(gr2.id) not in (inv.gr_ids or [])


@pytest.mark.asyncio
async def test_header_level_match_links_every_gr_on_that_po():
    """Total-value matching carries no line information, so PO-level is the only
    available granularity."""
    async with sm.AsyncSessionLocal() as db:
        user, vendor, po = await _fixture(db, subtotal="130")
        line1 = _po_line(po.id, "A", "5", "50")
        line2 = _po_line(po.id, "B", "8", "80")
        db.add_all([line1, line2])
        await db.flush()

        gr1 = await _gr_with_line(db, po, vendor.id, user.id, line1.id, "50")
        gr2 = await _gr_with_line(db, po, vendor.id, user.id, line2.id, "80")

        inv = _invoice(vendor.id, user.id, amount="130")
        db.add(inv)
        await db.flush()

        req = InvoiceMatchRequest(allocations=[AllocationInput(
            invoice_line_id=uuid.uuid4(), po_id=po.id, po_line_id=None,
            allocated_amount=Decimal("130"),
        )])
        await invoice_crud.match(db, inv, req, matched_by=user.id, auto_link_grs=True)
        await db.refresh(inv)

        assert set(inv.gr_ids) == {str(gr1.id), str(gr2.id)}
        assert inv.gr_value == Decimal("130.00")


@pytest.mark.asyncio
async def test_cancelled_and_rejected_grs_are_never_auto_linked():
    async with sm.AsyncSessionLocal() as db:
        user, vendor, po = await _fixture(db)
        line = _po_line(po.id, "A", "5", "50")
        db.add(line)
        await db.flush()

        await _gr_with_line(db, po, vendor.id, user.id, line.id, "50", status="cancelled")
        await _gr_with_line(db, po, vendor.id, user.id, line.id, "50", status="rejected")
        good = await _gr_with_line(db, po, vendor.id, user.id, line.id, "50", status="collected")

        inv = _invoice(vendor.id, user.id, amount="50")
        db.add(inv)
        await db.flush()

        req = InvoiceMatchRequest(allocations=[AllocationInput(
            invoice_line_id=uuid.uuid4(), po_id=po.id, po_line_id=line.id,
            allocated_amount=Decimal("50"),
        )])
        await invoice_crud.match(db, inv, req, matched_by=user.id, auto_link_grs=True)
        await db.refresh(inv)

        assert inv.gr_ids == [str(good.id)]


@pytest.mark.asyncio
async def test_explicit_gr_selection_wins_over_auto_discovery():
    async with sm.AsyncSessionLocal() as db:
        user, vendor, po = await _fixture(db, subtotal="130")
        line1 = _po_line(po.id, "A", "5", "50")
        line2 = _po_line(po.id, "B", "8", "80")
        db.add_all([line1, line2])
        await db.flush()

        gr1 = await _gr_with_line(db, po, vendor.id, user.id, line1.id, "50")
        gr2 = await _gr_with_line(db, po, vendor.id, user.id, line2.id, "80")

        inv = _invoice(vendor.id, user.id, amount="50")
        db.add(inv)
        await db.flush()

        # Caller picked gr2 deliberately, even though line routing points at gr1.
        req = InvoiceMatchRequest(
            allocations=[AllocationInput(
                invoice_line_id=uuid.uuid4(), po_id=po.id, po_line_id=line1.id,
                allocated_amount=Decimal("50"),
            )],
            gr_ids=[gr2.id],
        )
        await invoice_crud.match(db, inv, req, matched_by=user.id, auto_link_grs=True)
        await db.refresh(inv)

        assert inv.gr_ids == [str(gr2.id)]
        assert str(gr1.id) not in inv.gr_ids


@pytest.mark.asyncio
async def test_rematch_does_not_resurrect_a_cleared_gr_selection():
    """Clearing the GR link in the edit form must stick: the edit path replays
    match() through rematch_from_existing, which never auto-discovers."""
    async with sm.AsyncSessionLocal() as db:
        user, vendor, po = await _fixture(db)
        line = _po_line(po.id, "A", "5", "50")
        db.add(line)
        await db.flush()
        await _gr_with_line(db, po, vendor.id, user.id, line.id, "50")

        inv = _invoice(vendor.id, user.id, amount="50")
        db.add(inv)
        await db.flush()
        req = InvoiceMatchRequest(allocations=[AllocationInput(
            invoice_line_id=uuid.uuid4(), po_id=po.id, po_line_id=line.id,
            allocated_amount=Decimal("50"),
        )])
        await invoice_crud.match(db, inv, req, matched_by=user.id, auto_link_grs=True)
        await db.refresh(inv)
        assert inv.gr_ids  # auto-linked on the first match

        # User clears the selection in the edit form, then the edit re-matches.
        await invoice_crud._apply_gr_selection(db, inv, [])
        await db.flush()
        await invoice_crud.rematch_from_existing(db, inv, matched_by=user.id)
        await db.refresh(inv)

        assert inv.gr_ids is None
        assert inv.gr_id is None


@pytest.mark.asyncio
async def test_match_without_the_flag_still_ignores_existing_grs():
    """Default behaviour is unchanged for every other caller of match()."""
    async with sm.AsyncSessionLocal() as db:
        user, vendor, po = await _fixture(db)
        line = _po_line(po.id, "A", "5", "50")
        db.add(line)
        await db.flush()
        await _gr_with_line(db, po, vendor.id, user.id, line.id, "50")

        inv = _invoice(vendor.id, user.id, amount="50")
        db.add(inv)
        await db.flush()
        req = InvoiceMatchRequest(allocations=[AllocationInput(
            invoice_line_id=uuid.uuid4(), po_id=po.id, po_line_id=line.id,
            allocated_amount=Decimal("50"),
        )])
        await invoice_crud.match(db, inv, req, matched_by=user.id)
        await db.refresh(inv)

        assert inv.gr_ids is None
