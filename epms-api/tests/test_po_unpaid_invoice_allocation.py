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


@pytest.mark.asyncio
async def test_po_whose_only_invoice_is_claimed_reports_nothing_to_pay(admin_client):
    """一张 PO 的发票已被某张在途 PA 认领 → 它已经无款可付。

    发票状态还不是 paid(占着它的 PA 还没走完付款),只看 status != 'paid'
    会让这张 PO 继续出现在 PA 创建页的 PO 选择器和 PO 页的 Create PA 按钮上,
    点进去却一张发票也勾不动 —— 那张发票锁在别人的 PA 上。
    """
    from app.models.pa import PaymentApplication

    async with sm.AsyncSessionLocal() as db:
        u = await _user(db)
        v = await _vendor(db)
        po = await _po(db, v, u.id)
        inv = Invoice(
            internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
            vendor_id=v.id, vendor_name="Acme", amount=Decimal("200"),
            tax_amount=Decimal("0"), total_amount=Decimal("200"),
            invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            status="matched", line_items=[], po_id=po.id,
            po_number=po.number, uploaded_by=u.id)
        db.add(inv)
        await db.flush()
        db.add(PaymentApplication(
            pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="Claiming PA",
            po_id=po.id, po_number=po.number,
            vendor_id=v.id, vendor_name="Acme",
            invoice_ids=[str(inv.id)], gr_ids=[],
            subtotal=Decimal("200"), payment_amount=Decimal("200"),
            status="submitted", created_by=u.id))
        await db.commit()
        number, po_id = po.number, po.id

    resp = await admin_client.get("/api/v1/po", params={"page_size": 200})
    by_number = {p["number"]: p for p in resp.json()["items"]}
    assert by_number[number]["has_unpaid_invoice"] is False, (
        "发票已被在途 PA 认领,这张 PO 不该再宣称有款可付"
    )

    # 详情端点必须给出同一个答案 —— PO 页的 Create PA 按钮读的是它,
    # 两处不一致就等于按钮把人送进一个空的付款页。
    detail = await admin_client.get(f"/api/v1/po/{po_id}")
    assert detail.status_code == 200
    assert detail.json()["has_unpaid_invoice"] is False


@pytest.mark.asyncio
async def test_cancelled_pa_releases_its_invoice(admin_client):
    """作废的 PA 不再占着发票 —— 否则一次误建就把这张 PO 永久变成不可付。"""
    from app.models.pa import PaymentApplication

    async with sm.AsyncSessionLocal() as db:
        u = await _user(db)
        v = await _vendor(db)
        po = await _po(db, v, u.id)
        inv = Invoice(
            internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
            vendor_id=v.id, vendor_name="Acme", amount=Decimal("200"),
            tax_amount=Decimal("0"), total_amount=Decimal("200"),
            invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            status="matched", line_items=[], po_id=po.id,
            po_number=po.number, uploaded_by=u.id)
        db.add(inv)
        await db.flush()
        db.add(PaymentApplication(
            pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="Cancelled PA",
            po_id=po.id, po_number=po.number,
            vendor_id=v.id, vendor_name="Acme",
            invoice_ids=[str(inv.id)], gr_ids=[],
            subtotal=Decimal("200"), payment_amount=Decimal("200"),
            status="cancelled", created_by=u.id))
        await db.commit()
        number = po.number

    resp = await admin_client.get("/api/v1/po", params={"page_size": 200})
    by_number = {p["number"]: p for p in resp.json()["items"]}
    assert by_number[number]["has_unpaid_invoice"] is True


@pytest.mark.asyncio
async def test_a_second_unclaimed_invoice_keeps_the_po_payable(admin_client):
    """认领是按发票算的,不是按 PO 算的:还有一张没人认领的发票就仍然可付。"""
    from app.models.pa import PaymentApplication

    async with sm.AsyncSessionLocal() as db:
        u = await _user(db)
        v = await _vendor(db)
        po = await _po(db, v, u.id)
        invs = []
        for _ in range(2):
            inv = Invoice(
                internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
                vendor_id=v.id, vendor_name="Acme", amount=Decimal("100"),
                tax_amount=Decimal("0"), total_amount=Decimal("100"),
                invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
                status="matched", line_items=[], po_id=po.id,
                po_number=po.number, uploaded_by=u.id)
            db.add(inv)
            await db.flush()
            invs.append(inv)
        db.add(PaymentApplication(
            pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="Claims one",
            po_id=po.id, po_number=po.number,
            vendor_id=v.id, vendor_name="Acme",
            invoice_ids=[str(invs[0].id)], gr_ids=[],
            subtotal=Decimal("100"), payment_amount=Decimal("100"),
            status="approved", created_by=u.id))
        await db.commit()
        number = po.number

    resp = await admin_client.get("/api/v1/po", params={"page_size": 200})
    by_number = {p["number"]: p for p in resp.json()["items"]}
    assert by_number[number]["has_unpaid_invoice"] is True
