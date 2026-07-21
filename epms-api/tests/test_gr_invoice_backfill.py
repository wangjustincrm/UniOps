"""GR create line-item reverse-matches to the invoice billing the same PO lines.

A GR's lines carry po_line_id (chosen from the PO); a matched invoice's
allocations carry the same po_line_id. So a newly-created GR attaches only to the
invoice(s) for the lines it received — correct with multiple GRs per PO — and a
second GR on the same line accumulates onto that invoice. Convenience only: it
never changes match status.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

import app.db.session as sm
from app.crud import gr as gr_crud
from app.crud import user as user_crud
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.po import PoLineItem, PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


def _invoice(po_id, vendor_id, uploaded_by, *, ref, status, gr_ids=None):
    return Invoice(
        internal_ref=ref, vendor_invoice_number=ref, vendor_id=vendor_id,
        vendor_name="Acme", amount=Decimal("100"), tax_amount=Decimal("0"),
        total_amount=Decimal("100"), invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1), status=status, line_items=[],
        po_id=po_id, gr_ids=gr_ids, uploaded_by=uploaded_by,
    )


def _po_line(po_id, desc, unit_price, line_total):
    return PoLineItem(
        po_id=po_id, description=desc, qty=Decimal("10"), unit="ea",
        unit_price=Decimal(unit_price), line_total=Decimal(line_total),
    )


def _alloc(invoice_id, po_id, po_line_id, amount):
    return InvoicePoAllocation(
        invoice_id=invoice_id, invoice_line_id=uuid.uuid4(), po_id=po_id,
        po_line_id=po_line_id, allocated_amount=Decimal(amount),
        allocated_total=Decimal(amount),
    )


def _gr(po, vendor_id, created_by):
    return GoodsReceipt(
        number=f"GR-{uuid.uuid4().hex[:8]}", title="Goods", po_id=po.id,
        po_number=po.number, vendor_id=vendor_id, vendor_name="Acme",
        gr_type="physical", procurement_type=2, status="pending_ack",
        created_by=created_by,
    )


def _gr_line(gr_id, po_line_id, line_total):
    return GrLineItem(
        gr_id=gr_id, po_line_id=po_line_id, description="widget",
        qty_ordered=Decimal("10"), qty_received=Decimal("10"), unit="ea",
        unit_price=Decimal("5"), line_total=Decimal(line_total),
    )


@pytest.mark.asyncio
async def test_gr_autofills_only_the_invoice_for_its_lines_and_accumulates():
    async with sm.AsyncSessionLocal() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"gr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="GR Tester", role="warehouse_staff",
        ))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme",
                        category="supplier", contact_name="C", contact_email="c@x.com")
        db.add(vendor)
        await db.flush()

        po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=2,
                           vendor_id=vendor.id, vendor_name="Acme", status="issued",
                           created_by=user.id)
        db.add(po)
        await db.flush()

        line1 = _po_line(po.id, "A", "5", "50")   # billed by inv1
        line2 = _po_line(po.id, "B", "8", "80")   # billed by inv2
        db.add_all([line1, line2])
        await db.flush()

        inv1 = _invoice(po.id, vendor.id, user.id, ref=f"INV1-{uuid.uuid4().hex[:6]}", status="matched")
        inv2 = _invoice(po.id, vendor.id, user.id, ref=f"INV2-{uuid.uuid4().hex[:6]}", status="matched")
        inv3 = _invoice(po.id, vendor.id, user.id, ref=f"INV3-{uuid.uuid4().hex[:6]}", status="unmatched")
        db.add_all([inv1, inv2, inv3])
        await db.flush()
        db.add_all([_alloc(inv1.id, po.id, line1.id, "50"),
                    _alloc(inv2.id, po.id, line2.id, "80")])
        await db.flush()

        # GR-A receives line1 only → should reach inv1 only
        grA = _gr(po, vendor.id, user.id)
        db.add(grA)
        await db.flush()
        grA_lines = [_gr_line(grA.id, line1.id, "50")]
        db.add_all(grA_lines)
        await db.flush()
        await gr_crud._autofill_gr_to_matched_invoices(db, grA, grA_lines)
        await db.flush()
        for inv in (inv1, inv2, inv3):
            await db.refresh(inv)

        assert inv1.gr_ids == [str(grA.id)]
        assert inv1.gr_value == Decimal("50.00")
        assert inv2.gr_ids is None          # bills line2, not received by GR-A
        assert inv3.gr_ids is None          # unmatched, no allocation

        # GR-B is a second partial receipt of line1 → inv1 accumulates
        grB = _gr(po, vendor.id, user.id)
        db.add(grB)
        await db.flush()
        grB_lines = [_gr_line(grB.id, line1.id, "30")]
        db.add_all(grB_lines)
        await db.flush()
        await gr_crud._autofill_gr_to_matched_invoices(db, grB, grB_lines)
        await db.flush()
        await db.refresh(inv1)

        assert set(inv1.gr_ids) == {str(grA.id), str(grB.id)}
        assert inv1.gr_value == Decimal("80.00")   # 50 + 30 across both GRs


@pytest.mark.asyncio
async def test_partial_overlap_fans_gr_out_to_every_shared_invoice():
    """PO lines A,B,C. GR1 receives {A,C}; GR2 receives {B,C}. INV1 bills {A,B};
    INV2 bills {C}. A GR links to an invoice on ANY shared line (no full-coverage
    requirement), so both GRs end up on both invoices.
    """
    async with sm.AsyncSessionLocal() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"gr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="GR Tester", role="warehouse_staff",
        ))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme",
                        category="supplier", contact_name="C", contact_email="c@x.com")
        db.add(vendor)
        await db.flush()

        po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=2,
                           vendor_id=vendor.id, vendor_name="Acme", status="issued",
                           created_by=user.id)
        db.add(po)
        await db.flush()

        lineA = _po_line(po.id, "A", "5", "50")
        lineB = _po_line(po.id, "B", "8", "80")
        lineC = _po_line(po.id, "C", "2", "20")
        db.add_all([lineA, lineB, lineC])
        await db.flush()

        inv1 = _invoice(po.id, vendor.id, user.id, ref=f"INV1-{uuid.uuid4().hex[:6]}", status="matched")
        inv2 = _invoice(po.id, vendor.id, user.id, ref=f"INV2-{uuid.uuid4().hex[:6]}", status="matched")
        db.add_all([inv1, inv2])
        await db.flush()
        db.add_all([
            _alloc(inv1.id, po.id, lineA.id, "50"),   # INV1 bills A
            _alloc(inv1.id, po.id, lineB.id, "80"),   # INV1 bills B
            _alloc(inv2.id, po.id, lineC.id, "20"),   # INV2 bills C
        ])
        await db.flush()

        # GR1 receives A + C
        gr1 = _gr(po, vendor.id, user.id)
        db.add(gr1)
        await db.flush()
        gr1_lines = [_gr_line(gr1.id, lineA.id, "50"), _gr_line(gr1.id, lineC.id, "20")]  # total 70
        db.add_all(gr1_lines)
        await db.flush()
        await gr_crud._autofill_gr_to_matched_invoices(db, gr1, gr1_lines)

        # GR2 receives B + C
        gr2 = _gr(po, vendor.id, user.id)
        db.add(gr2)
        await db.flush()
        gr2_lines = [_gr_line(gr2.id, lineB.id, "80"), _gr_line(gr2.id, lineC.id, "30")]  # total 110
        db.add_all(gr2_lines)
        await db.flush()
        await gr_crud._autofill_gr_to_matched_invoices(db, gr2, gr2_lines)
        await db.flush()
        await db.refresh(inv1)
        await db.refresh(inv2)

        # Both GRs share a line with each invoice → both land on both invoices.
        assert set(inv1.gr_ids) == {str(gr1.id), str(gr2.id)}
        assert set(inv2.gr_ids) == {str(gr1.id), str(gr2.id)}
        # gr_value is the whole-GR sum across all linked GRs (70 + 110), matching
        # match()'s existing decorative semantics (not scoped to the billed lines).
        assert inv1.gr_value == Decimal("180.00")
        assert inv2.gr_value == Decimal("180.00")
