"""po_has_three_way_matched_invoice: True 仅当 matched 发票已挂 gr_id。"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

import app.db.session as sm
from app.crud import user as user_crud
from app.crud.po import po_has_three_way_matched_invoice
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


def _invoice(po_id, vendor_id, uploaded_by, *, ref, status, gr_id=None):
    return Invoice(
        internal_ref=ref, vendor_invoice_number=ref, vendor_id=vendor_id,
        vendor_name="Acme", amount=Decimal("100"), tax_amount=Decimal("0"),
        total_amount=Decimal("100"), invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1), status=status, line_items=[],
        po_id=po_id, gr_id=gr_id, uploaded_by=uploaded_by,
    )


async def _make_po(db):
    user = await user_crud.create(db, RegisterRequest(
        email=f"tw-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="TW Tester", role="warehouse_staff",
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
    return po, vendor, user


async def _make_gr(db, po, created_by):
    gr = GoodsReceipt(
        number=f"GR-{uuid.uuid4().hex[:8]}", title="Test GR",
        po_id=po.id, po_number=po.number, vendor_id=po.vendor_id,
        vendor_name=po.vendor_name, gr_type="standard", procurement_type=1,
        currency="CAD", status="pending_ack", created_by=created_by
    )
    db.add(gr)
    await db.flush()
    return gr


@pytest.mark.asyncio
async def test_three_way_true_only_when_matched_and_gr_linked():
    async with sm.AsyncSessionLocal() as db:
        po, vendor, user = await _make_po(db)
        inv = _invoice(po.id, vendor.id, user.id, ref=f"I-{uuid.uuid4().hex[:6]}",
                       status="matched", gr_id=None)
        db.add(inv)
        await db.flush()
        # matched 但无 GR → 非 3-way
        assert await po_has_three_way_matched_invoice(db, po.id) is False
        # 挂上 GR(gr_id) → 3-way
        gr = await _make_gr(db, po, user.id)
        inv.gr_id = gr.id
        await db.flush()
        assert await po_has_three_way_matched_invoice(db, po.id) is True


@pytest.mark.asyncio
async def test_three_way_false_for_exception_status():
    async with sm.AsyncSessionLocal() as db:
        po, vendor, user = await _make_po(db)
        gr = await _make_gr(db, po, user.id)
        db.add(_invoice(po.id, vendor.id, user.id, ref=f"I-{uuid.uuid4().hex[:6]}",
                        status="exception", gr_id=gr.id))
        await db.flush()
        assert await po_has_three_way_matched_invoice(db, po.id) is False
