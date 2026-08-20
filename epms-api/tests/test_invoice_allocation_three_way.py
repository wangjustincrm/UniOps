"""行级分摊 PO(invoice_po_allocations)必须和表头 PO 一样进入 3-way / create_pa 口径。

生产 bug(PO-400-2607-12):一张发票分摊到两个 PO,只有表头那个 PO 拿到
create_pa 任务,分摊 PO 既无 create_pa 也无 confirm_receipt,收货闸门还会 422
挡住手工补建 —— 该 PO 的货款静默漏付。三条路径(match hook / GR hook /
inbox backfill)都只查 Invoice.po_id,谁都看不到 invoice_po_allocations。
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.db.session as sm
from app.api.v1.invoices import _on_invoice_matched
from app.crud import user as user_crud
from app.crud.gr import _on_three_way_reached
from app.crud.po import po_has_three_way_matched_invoice
from app.crud.task import _backfill_create_pa_tasks
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
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
    db.add(v)
    await db.flush()
    return v


async def _pr_po(db, vendor, requester, *, po_type=2):
    """PR + PO(requester 是 PR 发起人 → create_pa 会指派给他)。"""
    pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:8]}", title="T",
                         type=po_type, created_by=requester.id)
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=po_type,
                       vendor_id=vendor.id, vendor_name="Acme", status="fully_received",
                       created_by=requester.id, pr_id=pr.id, total=Decimal("100"))
    db.add(po)
    await db.flush()
    return pr, po


async def _gr(db, po, creator):
    gr = GoodsReceipt(number=f"GR-{uuid.uuid4().hex[:8]}", title="G", po_id=po.id,
                      po_number=po.number, vendor_id=po.vendor_id, vendor_name="Acme",
                      gr_type="physical", procurement_type=po.type, status="collected",
                      created_by=creator.id)
    db.add(gr)
    await db.flush()
    return gr


async def _split_invoice(db, header_po, alloc_po, vendor, uploader, *, gr_id=None):
    """一张发票:表头挂 header_po,另有一行分摊到 alloc_po(生产 INV-2026-0157 形态)。"""
    inv = Invoice(
        internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
        vendor_id=vendor.id, vendor_name="Acme", amount=Decimal("200"),
        tax_amount=Decimal("0"), total_amount=Decimal("200"),
        invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1), status="matched",
        line_items=[], po_id=header_po.id, po_number=header_po.number,
        gr_id=gr_id, uploaded_by=uploader.id)
    db.add(inv)
    await db.flush()
    for po in (header_po, alloc_po):
        db.add(InvoicePoAllocation(
            invoice_id=inv.id, invoice_line_id=uuid.uuid4(), po_id=po.id,
            po_line_id=None, allocated_amount=Decimal("100"),
            allocated_tax=Decimal("0"), allocated_total=Decimal("100")))
    await db.flush()
    return inv


async def _open(db, po_id, ttype):
    return (await db.execute(select(Task).where(
        Task.type == ttype, Task.document_id == po_id,
        Task.is_completed.is_(False)))).scalars().all()


# ── 1. helper:分摊 PO 也算 3-way(前提是该 PO 自己收了货) ────────────────────

@pytest.mark.asyncio
async def test_three_way_true_for_allocation_only_po_with_gr():
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        _, header_po = await _pr_po(db, v, req)
        _, alloc_po = await _pr_po(db, v, req)
        gr = await _gr(db, alloc_po, req)                    # 收的是分摊 PO 的货
        await _split_invoice(db, header_po, alloc_po, v, req, gr_id=gr.id)
        assert await po_has_three_way_matched_invoice(db, alloc_po.id) is True


@pytest.mark.asyncio
async def test_three_way_false_for_allocation_only_po_without_gr():
    """分摊 PO 自己没收货 → 不能因为别的 PO 的 GR 就放行付款。"""
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        _, header_po = await _pr_po(db, v, req)
        _, alloc_po = await _pr_po(db, v, req)
        gr = await _gr(db, header_po, req)                   # 只有表头 PO 收了货
        await _split_invoice(db, header_po, alloc_po, v, req, gr_id=gr.id)
        assert await po_has_three_way_matched_invoice(db, header_po.id) is True
        assert await po_has_three_way_matched_invoice(db, alloc_po.id) is False


# ── 2. match hook:分摊 PO 也要分流 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_match_hook_creates_create_pa_for_allocated_po():
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        _, header_po = await _pr_po(db, v, req)
        _, alloc_po = await _pr_po(db, v, req)
        await _gr(db, header_po, req)
        gr = await _gr(db, alloc_po, req)
        inv = await _split_invoice(db, header_po, alloc_po, v, req, gr_id=gr.id)
        await _on_invoice_matched(db, inv)
        await db.flush()
        for po in (header_po, alloc_po):
            cp = await _open(db, po.id, "create_pa")
            assert len(cp) == 1, f"{po.number} 应有 create_pa"
            assert cp[0].assigned_user_id == req.id


@pytest.mark.asyncio
async def test_match_hook_creates_confirm_receipt_for_unreceived_allocated_po():
    """表头 PO 已收货、分摊 PO 未收货 → 分摊 PO 拿催收货,不是 create_pa。"""
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        _, header_po = await _pr_po(db, v, req)
        _, alloc_po = await _pr_po(db, v, req)
        gr = await _gr(db, header_po, req)
        inv = await _split_invoice(db, header_po, alloc_po, v, req, gr_id=gr.id)
        await _on_invoice_matched(db, inv)
        await db.flush()
        assert len(await _open(db, header_po.id, "create_pa")) == 1
        assert await _open(db, alloc_po.id, "create_pa") == []
        assert len(await _open(db, alloc_po.id, "confirm_receipt")) == 1


# ── 3. GR hook:给分摊 PO 建 GR 后要补 create_pa(生产 PO-400-2607-12 的原路径) ──

@pytest.mark.asyncio
async def test_gr_hook_creates_create_pa_for_allocation_only_po():
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        _, header_po = await _pr_po(db, v, req)
        _, alloc_po = await _pr_po(db, v, req)
        gr = await _gr(db, alloc_po, req)
        await _split_invoice(db, header_po, alloc_po, v, req, gr_id=gr.id)
        await _on_three_way_reached(db, alloc_po.id)
        await db.flush()
        cp = await _open(db, alloc_po.id, "create_pa")
        assert len(cp) == 1
        assert cp[0].assigned_user_id == req.id


# ── 4. inbox 自愈 backfill:补上存量漏建 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_backfill_covers_allocation_only_po():
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        _, header_po = await _pr_po(db, v, req)
        _, alloc_po = await _pr_po(db, v, req)
        gr = await _gr(db, alloc_po, req)
        await _split_invoice(db, header_po, alloc_po, v, req, gr_id=gr.id)
        await _backfill_create_pa_tasks(db)
        await db.flush()
        cp = await _open(db, alloc_po.id, "create_pa")
        assert len(cp) == 1
        assert cp[0].assigned_user_id == req.id


@pytest.mark.asyncio
async def test_backfill_skips_allocation_only_po_without_gr():
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        _, header_po = await _pr_po(db, v, req)
        _, alloc_po = await _pr_po(db, v, req)
        gr = await _gr(db, header_po, req)
        await _split_invoice(db, header_po, alloc_po, v, req, gr_id=gr.id)
        await _backfill_create_pa_tasks(db)
        await db.flush()
        assert await _open(db, alloc_po.id, "create_pa") == []
