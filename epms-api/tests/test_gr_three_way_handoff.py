"""_on_three_way_reached: 关 confirm_receipt + 建 create_pa(3-way);已有 PA 则幂等。"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.db.session as sm
from app.crud import user as user_crud
from app.crud.gr import _on_three_way_reached
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _setup(db):
    req = await user_crud.create(db, RegisterRequest(
        email=f"r-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="R", role="requester"))
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v); await db.flush()
    pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:8]}", title="T", type=2, created_by=req.id)
    db.add(pr); await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=2,
                       vendor_id=v.id, vendor_name="Acme", status="issued",
                       created_by=req.id, pr_id=pr.id)
    db.add(po); await db.flush()
    return po, v, req


async def _matched_inv_with_gr(db, po, v, req):
    gr = GoodsReceipt(number=f"GR-{uuid.uuid4().hex[:8]}", title="G", po_id=po.id,
                      po_number=po.number, vendor_id=v.id, vendor_name="Acme",
                      gr_type="physical", procurement_type=2, status="collected", created_by=req.id)
    db.add(gr); await db.flush()
    inv = Invoice(internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
                  vendor_id=v.id, vendor_name="Acme", amount=Decimal("100"), tax_amount=Decimal("0"),
                  total_amount=Decimal("100"), invoice_date=date(2026,1,1), due_date=date(2026,2,1),
                  status="matched", line_items=[], po_id=po.id, gr_id=gr.id, uploaded_by=req.id)
    db.add(inv); await db.flush()


def _confirm_task(po):
    return Task(type="confirm_receipt", priority="normal", document_type="po",
                document_id=po.id, document_number=po.number,
                assigned_role="warehouse_staff", title="Confirm goods receipt", vendor="Acme")


async def _open(db, po_id, ttype):
    return (await db.execute(select(Task).where(
        Task.type == ttype, Task.document_id == po_id,
        Task.is_completed.is_(False)))).scalars().all()


@pytest.mark.asyncio
async def test_three_way_reached_closes_confirm_receipt_and_creates_create_pa():
    async with sm.AsyncSessionLocal() as db:
        po, v, req = await _setup(db)
        db.add(_confirm_task(po)); await db.flush()
        await _matched_inv_with_gr(db, po, v, req)          # 3-way
        await _on_three_way_reached(db, po.id)
        await db.flush()
        assert await _open(db, po.id, "confirm_receipt") == []      # 关掉催办
        cp = await _open(db, po.id, "create_pa")
        assert len(cp) == 1
        assert cp[0].assigned_user_id == req.id


@pytest.mark.asyncio
async def test_three_way_reached_idempotent_when_pa_exists():
    async with sm.AsyncSessionLocal() as db:
        po, v, req = await _setup(db)
        db.add(_confirm_task(po)); await db.flush()
        await _matched_inv_with_gr(db, po, v, req)
        db.add(PaymentApplication(pa_number=f"PA-{uuid.uuid4().hex[:6]}", title="T", po_id=po.id,
               po_number=po.number, vendor_id=v.id, vendor_name="Acme", subtotal=Decimal("100"),
               payment_amount=Decimal("100"), currency="CAD", status="draft", created_by=req.id))
        await db.flush()
        await _on_three_way_reached(db, po.id)
        await db.flush()
        assert await _open(db, po.id, "confirm_receipt") == []      # 仍关催办
        assert await _open(db, po.id, "create_pa") == []            # 已有 PA → 不建 create_pa
