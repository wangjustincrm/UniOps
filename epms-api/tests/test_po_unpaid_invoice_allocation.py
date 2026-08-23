"""GET /po 的 has_unpaid_invoice 必须认行级分摊,不能只认发票表头。

前端 PA 创建页的 PO 选择器只列 `is_prepaid || has_unpaid_invoice` 的 PO
(PaCreatePage.eligiblePos)。一票多 PO 时表头之外的 PO 在这个字段上是 false,
于是它们从选择器里消失 —— 生产上 Procurement Officer 代建 PA 时找不到
PO-400-2607-12 / PO-400-2607-10。
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

import app.db.session as sm
from app.crud import user as user_crud
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.po import PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _user(db):
    return await user_crud.create(db, RegisterRequest(
        email=f"po-alloc-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="PO Alloc Tester", role="procurement_officer"))


async def _vendor(db):
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v)
    await db.flush()
    return v


async def _po(db, vendor, creator_id):
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=2,
                       vendor_id=vendor.id, vendor_name="Acme",
                       status="fully_received", created_by=creator_id,
                       total=Decimal("100"))
    db.add(po)
    await db.flush()
    return po


@pytest.mark.asyncio
async def test_allocated_po_reports_unpaid_invoice(admin_client):
    """发票表头挂 PO-A、行级分摊到 PO-B → 两个 PO 都要 has_unpaid_invoice=True。"""
    async with sm.AsyncSessionLocal() as db:
        u = await _user(db)
        v = await _vendor(db)
        header_po = await _po(db, v, u.id)
        alloc_po = await _po(db, v, u.id)
        inv = Invoice(
            internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
            vendor_id=v.id, vendor_name="Acme", amount=Decimal("200"),
            tax_amount=Decimal("0"), total_amount=Decimal("200"),
            invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            status="matched", line_items=[], po_id=header_po.id,
            po_number=header_po.number, uploaded_by=u.id)
        db.add(inv)
        await db.flush()
        db.add(InvoicePoAllocation(
            invoice_id=inv.id, invoice_line_id=uuid.uuid4(), po_id=alloc_po.id,
            po_line_id=None, allocated_amount=Decimal("100"),
            allocated_tax=Decimal("0"), allocated_total=Decimal("100")))
        await db.commit()
        header_number, alloc_number = header_po.number, alloc_po.number

    resp = await admin_client.get("/api/v1/po", params={"page_size": 200})
    assert resp.status_code == 200
    by_number = {p["number"]: p for p in resp.json()["items"]}
    assert by_number[header_number]["has_unpaid_invoice"] is True
    assert by_number[alloc_number]["has_unpaid_invoice"] is True, (
        "行级分摊的 PO 也有待付发票,否则它在 PA 创建页的 PO 选择器里消失"
    )


@pytest.mark.asyncio
async def test_po_without_any_invoice_reports_no_unpaid_invoice(admin_client):
    """没有任何发票(表头或分摊)的 PO 不能被误标成有待付发票。"""
    async with sm.AsyncSessionLocal() as db:
        u = await _user(db)
        v = await _vendor(db)
        bare_po = await _po(db, v, u.id)
        await db.commit()
        number = bare_po.number

    resp = await admin_client.get("/api/v1/po", params={"page_size": 200})
    assert resp.status_code == 200
    by_number = {p["number"]: p for p in resp.json()["items"]}
    assert by_number[number]["has_unpaid_invoice"] is False
