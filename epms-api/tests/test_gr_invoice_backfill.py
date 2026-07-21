"""GR confirm reverse-fills gr_value onto invoices matched before goods arrived.

Convenience backfill only — it must NOT change match status, and must respect
invoices that already reference a GR (and skip unmatched ones).
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
from app.models.po import PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


def _po(*, number, vendor_id, created_by):
    return PurchaseOrder(
        number=number, title="T", type=2,
        vendor_id=vendor_id, vendor_name="Acme", status="issued",
        created_by=created_by,
    )


def _invoice(po_id, vendor_id, uploaded_by, *, ref, status, gr_ids=None):
    return Invoice(
        internal_ref=ref, vendor_invoice_number=ref, vendor_id=vendor_id,
        vendor_name="Acme", amount=Decimal("100"), tax_amount=Decimal("0"),
        total_amount=Decimal("100"), invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1), status=status, line_items=[],
        po_id=po_id, gr_ids=gr_ids, uploaded_by=uploaded_by,
    )


@pytest.mark.asyncio
async def test_gr_confirm_backfills_gr_value_on_matched_invoice_without_gr():
    async with sm.AsyncSessionLocal() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"gr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="GR Tester", role="warehouse_staff",
        ))
        vendor = Vendor(
            code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
            contact_name="C", contact_email="c@example.com",
        )
        db.add(vendor)
        await db.flush()
        vendor_id = vendor.id

        po = _po(number=f"PO-{uuid.uuid4().hex[:8]}", vendor_id=vendor_id, created_by=user.id)
        db.add(po)
        await db.flush()

        # matched invoice with NO GR yet → should get the GR backfilled
        inv_waiting = _invoice(po.id, vendor_id, user.id, ref=f"INV-A-{uuid.uuid4().hex[:6]}",
                               status="matched", gr_ids=None)
        # matched invoice that already references another GR → must be left alone
        other_gr = str(uuid.uuid4())
        inv_linked = _invoice(po.id, vendor_id, user.id, ref=f"INV-B-{uuid.uuid4().hex[:6]}",
                              status="matched", gr_ids=[other_gr])
        # unmatched invoice (no allocations) → must be skipped
        inv_unmatched = _invoice(po.id, vendor_id, user.id, ref=f"INV-C-{uuid.uuid4().hex[:6]}",
                                 status="unmatched", gr_ids=None)
        db.add_all([inv_waiting, inv_linked, inv_unmatched])
        await db.flush()

        gr = GoodsReceipt(
            number=f"GR-{uuid.uuid4().hex[:8]}", title="Goods", po_id=po.id,
            po_number=po.number, vendor_id=vendor_id, vendor_name="Acme",
            gr_type="physical", procurement_type=2, status="collected",
            created_by=user.id,
        )
        gr.line_items = [GrLineItem(
            description="widget", qty_ordered=Decimal("10"), qty_received=Decimal("10"),
            unit="ea", unit_price=Decimal("7.50"), line_total=Decimal("75.00"),
        )]
        db.add(gr)
        await db.flush()

        await gr_crud._backfill_invoice_gr_value(db, gr)
        await db.flush()

        await db.refresh(inv_waiting)
        await db.refresh(inv_linked)
        await db.refresh(inv_unmatched)

        # waiting invoice picked up the GR + value; status unchanged
        assert inv_waiting.gr_ids == [str(gr.id)]
        assert inv_waiting.gr_id == gr.id
        assert inv_waiting.gr_value == Decimal("75.00")
        assert inv_waiting.status == "matched"
        # already-linked invoice untouched
        assert inv_linked.gr_ids == [other_gr]
        assert inv_linked.gr_value is None
        # unmatched invoice skipped
        assert inv_unmatched.gr_ids is None
        assert inv_unmatched.gr_value is None
