"""confirm_receipt 催收货任务的两个洞(生产 PO-089-2607-14 及另外 10 条僵尸任务)。

洞 A —— 建错:收货证据只认 `goods_receipts` 行。PMS 迁移来的历史单把收货量直接
写进 `po_line_items.received_qty`,库里根本没有 GR 行(生产 247 条
fully_received 里有 97 条如此),这些 PO 永远判"未收货",一 match 发票就长出一条
永远不会消失的催收货任务。

洞 B —— 关不掉:全仓只有 `crud.gr._on_three_way_reached` 会关 confirm_receipt,
而它只在 **GR 创建时**被调用。三方匹配从别的路径达成时(生产实例:AP 手工重
match 让 `gr_id` 落库并同时建了 create_pa),旁边那条 confirm_receipt 没人碰。

洞 C —— `review_match()` approve 把发票推到 matched 却不重跑 GR 自动发现,
`gr_id` 留空 → 下游三方判定看不到早就收进来的货(生产 22 张 matched 发票
`gr_id` 为空而其 PO 有 GR)。
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.db.session as sm
from app.api.v1.invoices import _on_invoice_matched
from app.crud import invoice as invoice_crud
from app.crud import user as user_crud
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.po import PoLineItem, PurchaseOrder
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


async def _po_with_pr(db, vendor, req, *, po_type=2, status="issued"):
    pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:8]}", title="T",
                         type=po_type, created_by=req.id)
    db.add(pr); await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=po_type,
                       vendor_id=vendor.id, vendor_name="Acme", status=status,
                       created_by=req.id, pr_id=pr.id)
    db.add(po); await db.flush()
    return po


async def _line(db, po, *, qty="1", received="0"):
    ln = PoLineItem(po_id=po.id, description="Widget", qty=Decimal(qty), unit="ea",
                    unit_price=Decimal("100"), line_total=Decimal("100"),
                    received_qty=Decimal(received))
    db.add(ln); await db.flush()
    return ln


async def _gr(db, po, vendor, creator, *, status="collected"):
    gr = GoodsReceipt(number=f"GR-{uuid.uuid4().hex[:8]}", title="G", po_id=po.id,
                      po_number=po.number, vendor_id=vendor.id, vendor_name="Acme",
                      gr_type="physical", procurement_type=2, status=status,
                      created_by=creator.id)
    db.add(gr); await db.flush()
    return gr


def _invoice(po, vendor, uploaded_by, *, gr_id=None, status="matched", line_id=None):
    return Invoice(
        internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
        vendor_id=vendor.id, vendor_name="Acme", amount=Decimal("100"),
        tax_amount=Decimal("0"), total_amount=Decimal("100"),
        invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1), status=status,
        line_items=([] if line_id is None else [{
            "id": str(line_id), "description": "Widget", "quantity": "1",
            "unit_price": "100", "line_total": "100"}]),
        po_id=po.id, gr_id=gr_id, uploaded_by=uploaded_by.id)


def _open_confirm_task(po):
    return Task(type="confirm_receipt", priority="normal", document_type="po",
                document_id=po.id, document_number=po.number,
                assigned_role="warehouse_staff",
                title=f"Confirm goods receipt for {po.number}", vendor="Acme")


async def _open(db, po_id, ttype):
    return (await db.execute(select(Task).where(
        Task.type == ttype, Task.document_id == po_id,
        Task.is_completed.is_(False)))).scalars().all()


# ── 洞 A:received_qty 也是收货证据 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_confirm_receipt_when_lines_already_received_without_gr_row():
    """PMS 迁移单:收货量已满、PO 已 fully_received,但库里没有 GR 行。
    货早就进来了,不该再催收货 —— 而且这些 PO 永远不会有 GR,催了也关不掉。"""
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        po = await _po_with_pr(db, v, req, status="fully_received")
        await _line(db, po, qty="7", received="7")          # 收货量已满,零 GR 行
        inv = _invoice(po, v, req, gr_id=None)
        db.add(inv); await db.flush()

        await _on_invoice_matched(db, inv)
        await db.flush()

        assert await _open(db, po.id, "confirm_receipt") == []


@pytest.mark.asyncio
async def test_open_confirm_receipt_closed_once_lines_show_receipt():
    """已经建错的那条,在下一次 match 时必须被关掉,而不是再发一封催办邮件。"""
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        po = await _po_with_pr(db, v, req, status="fully_received")
        await _line(db, po, qty="7", received="7")
        db.add(_open_confirm_task(po)); await db.flush()
        inv = _invoice(po, v, req, gr_id=None)
        db.add(inv); await db.flush()

        await _on_invoice_matched(db, inv)
        await db.flush()

        assert await _open(db, po.id, "confirm_receipt") == []


@pytest.mark.asyncio
async def test_confirm_receipt_still_raised_when_nothing_received():
    """反向对照:真没收货的 PO 仍然要催 —— 修洞不能把催办整个催没了。"""
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        po = await _po_with_pr(db, v, req)
        await _line(db, po, qty="6", received="0")          # 一件都没收
        inv = _invoice(po, v, req, gr_id=None)
        db.add(inv); await db.flush()

        await _on_invoice_matched(db, inv)
        await db.flush()

        cr = await _open(db, po.id, "confirm_receipt")
        assert len(cr) == 1
        assert cr[0].assigned_role == "warehouse_staff"


# ── 洞 B:三方从别的路径达成时也要关掉催办 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_create_pa_path_closes_open_confirm_receipt():
    """生产实例 PO-089-2607-14:AP 重新 match 让三方达成,同一次请求里建了
    create_pa —— 那条已经开着的 confirm_receipt 必须一起关掉。"""
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        v = await _vendor(db)
        po = await _po_with_pr(db, v, req)
        gr = await _gr(db, po, v, req)
        db.add(_open_confirm_task(po)); await db.flush()
        inv = _invoice(po, v, req, gr_id=gr.id)             # 3-way 达成
        db.add(inv); await db.flush()

        await _on_invoice_matched(db, inv)
        await db.flush()

        assert len(await _open(db, po.id, "create_pa")) == 1
        assert await _open(db, po.id, "confirm_receipt") == []


# ── 洞 C:review_match approve 要补挂 GR ──────────────────────────────────────

@pytest.mark.asyncio
async def test_review_match_approve_links_gr_received_while_pending_review():
    """发票 07-30 落在 match_review,货 08-05 才到。复核 approve 时必须把这批
    GR 挂上去,否则 gr_id 一直是 NULL,三方判定看不到已经收进来的货。"""
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        ap = await _user(db, "ap_clerk")
        v = await _vendor(db)
        po = await _po_with_pr(db, v, req)
        line = await _line(db, po, qty="1", received="1")
        inv_line_id = uuid.uuid4()
        inv = _invoice(po, v, req, gr_id=None, status="match_review",
                       line_id=inv_line_id)
        db.add(inv); await db.flush()
        db.add(InvoicePoAllocation(
            invoice_id=inv.id, invoice_line_id=inv_line_id,
            po_id=po.id, po_line_id=line.id,
            allocated_amount=Decimal("100"), allocated_tax=Decimal("0"),
            allocated_total=Decimal("100"), variance=Decimal("0"),
            variance_pct=Decimal("0")))
        gr = await _gr(db, po, v, req)                      # 货在复核期间到的
        db.add(GrLineItem(gr_id=gr.id, po_line_id=line.id, description="Widget",
                          qty_ordered=Decimal("1"), qty_received=Decimal("1"),
                          unit="ea", unit_price=Decimal("100"),
                          line_total=Decimal("100"), condition="good"))
        await db.flush()

        result = await invoice_crud.review_match(db, inv, "approve", None, ap.id)
        await db.flush()

        assert result.status == "matched"
        assert result.gr_id == gr.id
        assert result.gr_number == gr.number


@pytest.mark.asyncio
async def test_review_match_approve_then_dispatch_raises_create_pa_not_nudge():
    """端到端口径:复核 approve 之后走分流,应当是 create_pa,而不是催收货。"""
    async with sm.AsyncSessionLocal() as db:
        req = await _user(db, "requester")
        ap = await _user(db, "ap_clerk")
        v = await _vendor(db)
        po = await _po_with_pr(db, v, req)
        line = await _line(db, po, qty="1", received="1")
        inv_line_id = uuid.uuid4()
        inv = _invoice(po, v, req, gr_id=None, status="match_review",
                       line_id=inv_line_id)
        db.add(inv); await db.flush()
        db.add(InvoicePoAllocation(
            invoice_id=inv.id, invoice_line_id=inv_line_id,
            po_id=po.id, po_line_id=line.id,
            allocated_amount=Decimal("100"), allocated_tax=Decimal("0"),
            allocated_total=Decimal("100"), variance=Decimal("0"),
            variance_pct=Decimal("0")))
        gr = await _gr(db, po, v, req)
        db.add(GrLineItem(gr_id=gr.id, po_line_id=line.id, description="Widget",
                          qty_ordered=Decimal("1"), qty_received=Decimal("1"),
                          unit="ea", unit_price=Decimal("100"),
                          line_total=Decimal("100"), condition="good"))
        await db.flush()

        result = await invoice_crud.review_match(db, inv, "approve", None, ap.id)
        await _on_invoice_matched(db, result)
        await db.flush()

        assert len(await _open(db, po.id, "create_pa")) == 1
        assert await _open(db, po.id, "confirm_receipt") == []
