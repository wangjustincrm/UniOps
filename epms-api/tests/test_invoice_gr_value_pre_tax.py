"""3-Way Match tab: the receipt row is shown on the pre-tax basis of the PO row.

A PO mirrored from NC keeps NC's TAX-INCLUSIVE line amounts (norigtaxmny) but
a pre-tax header subtotal (norigmny); a GR's lines are priced from the PO
lines. So INV-2026-0243 read "PO 492.80 / GR 556.86 / Invoice 556.86" for the
same goods. attach_gr_value_pre_tax brings NC-priced receipt lines back to
pre-tax with the PO's own pre-tax share and leaves hand-entered lines alone.
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


async def _setup(db, *, nc: bool, subtotal: str, tax: str, line_total: str):
    user = await user_crud.create(db, RegisterRequest(
        email=f"gp-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="GR Tester", role="warehouse_staff",
    ))
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme",
                    category="supplier", contact_name="C", contact_email="c@x.com")
    db.add(vendor)
    await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=1 if nc else 2,
                       vendor_id=vendor.id, vendor_name="Acme", status="issued",
                       subtotal=Decimal(subtotal), tax_amount=Decimal(tax),
                       total=Decimal(subtotal) + Decimal(tax),
                       source="nc" if nc else None,
                       nc_source_pk=uuid.uuid4().hex[:20] if nc else None,
                       created_by=user.id)
    db.add(po)
    await db.flush()
    pl = PoLineItem(po_id=po.id, description="widget", qty=Decimal("1"), unit="ea",
                    unit_price=Decimal(line_total), line_total=Decimal(line_total),
                    nc_source_pk=uuid.uuid4().hex[:20] if nc else None)
    db.add(pl)
    await db.flush()
    gr = GoodsReceipt(number=f"GR-{uuid.uuid4().hex[:8]}", title="Goods", po_id=po.id,
                      po_number=po.number, vendor_id=vendor.id, vendor_name="Acme",
                      gr_type="physical", procurement_type=1 if nc else 2,
                      status="received", created_by=user.id)
    db.add(gr)
    await db.flush()
    db.add(GrLineItem(gr_id=gr.id, po_line_id=pl.id, description="widget",
                      qty_ordered=Decimal("1"), qty_received=Decimal("1"), unit="ea",
                      unit_price=Decimal(line_total), line_total=Decimal(line_total)))
    inv = Invoice(
        internal_ref=f"INV-{uuid.uuid4().hex[:8]}", vendor_invoice_number="X",
        vendor_id=vendor.id, vendor_name="Acme", amount=Decimal(subtotal),
        tax_amount=Decimal(tax), total_amount=Decimal(subtotal) + Decimal(tax),
        invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1), status="matched",
        line_items=[], po_id=po.id, gr_id=gr.id, gr_ids=[str(gr.id)],
        gr_value=Decimal(line_total), uploaded_by=user.id,
    )
    db.add(inv)
    await db.flush()
    return inv


@pytest.mark.asyncio
async def test_nc_po_receipt_is_brought_back_to_pre_tax():
    # The INV-2026-0243 shape: 13% tax, NC line carries the tax-inclusive amount.
    async with sm.AsyncSessionLocal() as db:
        inv = await _setup(db, nc=True, subtotal="492.80", tax="64.06", line_total="556.86")
        await invoice_crud.attach_gr_value_pre_tax(db, inv)
        assert inv.gr_value_pre_tax == Decimal("492.80")
        await db.rollback()


@pytest.mark.asyncio
async def test_uniops_po_receipt_is_already_pre_tax():
    # Positive control: a hand-entered PO's lines are pre-tax and stay untouched.
    async with sm.AsyncSessionLocal() as db:
        inv = await _setup(db, nc=False, subtotal="400.00", tax="52.00", line_total="400.00")
        await invoice_crud.attach_gr_value_pre_tax(db, inv)
        assert inv.gr_value_pre_tax == Decimal("400.00")
        await db.rollback()


@pytest.mark.asyncio
async def test_no_receipt_leaves_none():
    async with sm.AsyncSessionLocal() as db:
        inv = await _setup(db, nc=True, subtotal="100", tax="13", line_total="113")
        inv.gr_id = None
        inv.gr_ids = None
        await invoice_crud.attach_gr_value_pre_tax(db, inv)
        assert inv.gr_value_pre_tax is None
        await db.rollback()
