"""_on_invoice_matched: matched+GR → create_pa; matched+no GR → confirm_receipt
(物理→warehouse_staff 池, 服务→PR requester)。"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.db.session as sm
from app.api.v1.invoices import _on_invoice_matched
from app.crud import user as user_crud
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _user(db, role):
    return await user_crud.create(db, RegisterRequest(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name=f"T {role}", role=role))


async def _vendor(db):
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v); await db.flush()
    return v


async def _po(db, vendor, creator, *, po_type=2, pr_id=None):
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=po_type,
                       vendor_id=vendor.id, vendor_name="Acme", status="issued",
                       created_by=creator.id, pr_id=pr_id)
    db.add(po); await db.flush()
    return po


def _invoice(po_id, vendor_id, uploaded_by, *, gr_id=None, status="matched"):
    return Invoice(
        internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
        vendor_id=vendor_id, vendor_name="Acme", amount=Decimal("100"),
        tax_amount=Decimal("0"), total_amount=Decimal("100"),
        invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1), status=status,
        line_items=[], po_id=po_id, gr_id=gr_id, uploaded_by=uploaded_by)


async def _open(db, po_id, ttype):
    return (await db.execute(select(Task).where(
        Task.type == ttype, Task.document_id == po_id,
        Task.is_completed.is_(False)))).scalars().all()


@pytest.mark.asyncio
async def test_matched_no_gr_physical_creates_confirm_receipt_warehouse():
    async with sm.AsyncSessionLocal() as db:
        u = await _user(db, "warehouse_staff")
        v = await _vendor(db)
        po = await _po(db, v, u, po_type=2)                 # physical
        inv = _invoice(po.id, v.id, u.id, gr_id=None)
        db.add(inv); await db.flush()
        await _on_invoice_matched(db, inv)
        await db.flush()
        cr = await _open(db, po.id, "confirm_receipt")
        assert len(cr) == 1
        assert cr[0].assigned_role == "warehouse_staff"
        assert cr[0].assigned_user_id is None
        assert cr[0].document_type == "po"
        assert await _open(db, po.id, "create_pa") == []


@pytest.mark.asyncio
async def test_matched_no_gr_service_creates_confirm_receipt_requester():
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:8]}", title="T",
                             type=4, created_by=req.id)
        db.add(pr); await db.flush()
        po = await _po(db, v, req, po_type=4, pr_id=pr.id)  # service
        inv = _invoice(po.id, v.id, req.id, gr_id=None)
        db.add(inv); await db.flush()
        await _on_invoice_matched(db, inv)
        await db.flush()
        cr = await _open(db, po.id, "confirm_receipt")
        assert len(cr) == 1
        assert cr[0].assigned_role == "requester"
        assert cr[0].assigned_user_id == req.id


@pytest.mark.asyncio
async def test_matched_with_gr_creates_create_pa():
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:8]}", title="T",
                             type=2, created_by=req.id)
        db.add(pr); await db.flush()
        po = await _po(db, v, req, po_type=2, pr_id=pr.id)
        gr = GoodsReceipt(number=f"GR-{uuid.uuid4().hex[:8]}", title="G", po_id=po.id,
                          po_number=po.number, vendor_id=v.id, vendor_name="Acme",
                          gr_type="physical", procurement_type=2, status="collected",
                          created_by=req.id)
        db.add(gr); await db.flush()
        inv = _invoice(po.id, v.id, req.id, gr_id=gr.id)    # 3-way
        db.add(inv); await db.flush()
        await _on_invoice_matched(db, inv)
        await db.flush()
        cp = await _open(db, po.id, "create_pa")
        assert len(cp) == 1
        assert cp[0].assigned_role == "requester"
        assert cp[0].assigned_user_id == req.id
        assert await _open(db, po.id, "confirm_receipt") == []
