"""真实 gr_crud.create() 路径下的 3-way 交接:发票先 matched(未收货)、GR 后建,
必须留下 create_pa 任务。

现有 tests/test_gr_three_way_handoff.py 直接调 `_on_three_way_reached`,并且手工把
`Invoice.gr_id` 预置好——它验证的是那个函数本身,绕开了 create() 里真正的前置步骤
`_autofill_gr_to_matched_invoices`。生产 PO-192-2608-01 走的是完整路径:
  13:37 发票 matched(PO 尚无 GR)→ confirm_receipt 任务
  13:41 建 GR → confirm_receipt 被关掉,create_pa **没有**被建出来
且该 PO 状态停在 approved,不在 `_backfill_create_pa_tasks` 的候选状态里
(issued/partially_received/fully_received),读端自愈也永远捞不回来。
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.db.session as sm
from app.crud import gr as gr_crud
from app.crud import user as user_crud
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.schemas.gr import GrCreate, GrLineItemIn


async def _setup_po(db, *, po_type: int, po_status: str):
    """Service PO(type 4)+ PR + 一条 PO 行,状态可控。"""
    req = await user_crud.create(db, RegisterRequest(
        email=f"r-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Req Ester", role="requester"))
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v)
    await db.flush()
    pr = PurchaseRequest(number=f"PR-x{uuid.uuid4().hex[:6]}", title="T",
                         type=po_type, created_by=req.id)
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(number=f"PO-x{uuid.uuid4().hex[:6]}", title="T", type=po_type,
                       vendor_id=v.id, vendor_name="Acme", status=po_status,
                       created_by=req.id, pr_id=pr.id, subtotal=Decimal("100"),
                       total=Decimal("100"))
    db.add(po)
    await db.flush()
    line = PoLineItem(po_id=po.id, description="Service", qty=Decimal("1"),
                      unit="EA", unit_price=Decimal("100"), line_total=Decimal("100"),
                      sort_order=0)
    db.add(line)
    await db.flush()
    return po, line, v, req


async def _match_invoice_no_gr(db, po, po_line, v, req):
    """发票 matched 到 PO(行级分摊),此时 PO 还没有任何 GR —— 与生产时序一致。"""
    inv_line_id = uuid.uuid4()
    inv = Invoice(
        internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
        vendor_id=v.id, vendor_name="Acme", amount=Decimal("100"),
        tax_amount=Decimal("0"), total_amount=Decimal("100"),
        invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
        status="matched", match_route="po",
        line_items=[{"id": str(inv_line_id), "description": "Service",
                     "line_total": "100"}],
        po_id=po.id, uploaded_by=req.id,
        matched_at=datetime.now(timezone.utc),
    )
    db.add(inv)
    await db.flush()
    db.add(InvoicePoAllocation(
        invoice_id=inv.id, invoice_line_id=inv_line_id, po_id=po.id,
        po_line_id=po_line.id, allocated_amount=Decimal("100"),
        allocated_tax=Decimal("0"), allocated_total=Decimal("100")))
    await db.flush()
    return inv


def _confirm_receipt_task(po):
    """发票 matched 且未收货时 _on_invoice_matched 会建的那条催收货任务。"""
    return Task(type="confirm_receipt", priority="normal", document_type="po",
                document_id=po.id, document_number=po.number,
                assigned_role="requester", title=f"Confirm goods receipt for {po.number}",
                vendor="Acme")


async def _open_tasks(db, po_id, ttype):
    return (await db.execute(select(Task).where(
        Task.type == ttype, Task.document_type == "po",
        Task.document_id == po_id, Task.is_completed.is_(False)))).scalars().all()


async def _create_gr(db, po, po_line, req):
    return await gr_crud.create(
        db,
        GrCreate(po_id=po.id, title="Service delivered", line_items=[GrLineItemIn(
            po_line_id=po_line.id, description="Service", qty_ordered=Decimal("1"),
            qty_received=Decimal("1"), unit="EA", unit_price=Decimal("100"))]),
        po, req.id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("po_status", ["approved", "issued"])
async def test_gr_create_raises_create_pa_for_already_matched_invoice(po_status):
    """生产 PO-192-2608-01 的完整复现:matched-then-received 的服务 PO。

    `approved` 是生产那条的真实状态(服务 PO 从 approved 起就能建 GR,见
    api/v1/gr.py 的服务分支),`issued` 作为对照 —— 两者都必须建出 create_pa。
    """
    async with sm.AsyncSessionLocal() as db:
        po, po_line, v, req = await _setup_po(db, po_type=4, po_status=po_status)
        await _match_invoice_no_gr(db, po, po_line, v, req)
        db.add(_confirm_receipt_task(po))
        await db.flush()

        await _create_gr(db, po, po_line, req)
        await db.flush()

        # 催收货必须关掉(这一步生产上是对的)
        assert await _open_tasks(db, po.id, "confirm_receipt") == []
        # 而 create_pa 必须被建出来 —— 生产上缺的正是这一条
        create_pa = await _open_tasks(db, po.id, "create_pa")
        assert len(create_pa) == 1, (
            f"PO {po.number} ({po_status}) 已达成 3-way 却没有 create_pa 任务"
        )
        assert create_pa[0].assigned_user_id == req.id


async def _match_invoice_header_only(db, po, v, req):
    """PMS 导入的历史发票:表头挂 po_id、status=matched,但**没有任何分摊行**,
    gr_id 也是空。生产快照里有 419 张这样的发票。"""
    inv = Invoice(
        internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
        vendor_id=v.id, vendor_name="Acme", amount=Decimal("100"),
        tax_amount=Decimal("0"), total_amount=Decimal("100"),
        invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
        status="matched", match_route="po", line_items=[],
        po_id=po.id, uploaded_by=req.id, matched_at=datetime.now(timezone.utc),
    )
    db.add(inv)
    await db.flush()
    return inv


@pytest.mark.asyncio
async def test_gr_create_raises_create_pa_for_header_only_matched_invoice():
    """表头直连、零分摊行的 matched 发票,建 GR 后同样必须签出 create_pa。

    这类发票(PMS 批量导入)从来没有 invoice_po_allocations 行。三层逻辑都假设
    「matched 发票必有分摊行」,于是三层全部漏掉它:
      1. `_autofill_gr_to_matched_invoices` 只按分摊行找发票 → GR 不会挂上去,
         invoice.gr_id 永远为空;
      2. `po_has_three_way_matched_invoice` 表头分支要 gr_id 非空、分摊分支要有
         分摊行 → 两条都 False → `_on_three_way_reached` 直接 return;
      3. `crud/task.py::_backfill_create_pa_tasks` 的两条候选路径同样各要
         gr_id 非空 / 有分摊行 → 读端自愈也捞不回来。
    """
    async with sm.AsyncSessionLocal() as db:
        po, po_line, v, req = await _setup_po(db, po_type=2, po_status="issued")
        inv = await _match_invoice_header_only(db, po, v, req)
        db.add(_confirm_receipt_task(po))
        await db.flush()

        await _create_gr(db, po, po_line, req)
        await db.flush()

        await db.refresh(inv)
        assert inv.gr_id is not None, "GR 未回挂到表头直连的 matched 发票"
        create_pa = await _open_tasks(db, po.id, "create_pa")
        assert len(create_pa) == 1, (
            f"PO {po.number} 有 matched 发票且已收货,却没有 create_pa 任务"
        )
        assert create_pa[0].assigned_user_id == req.id


@pytest.mark.asyncio
@pytest.mark.parametrize("po_status,has_gr,expect", [
    ("approved", True, 1),    # 服务 PO 已收货却停在 approved —— 生产 PO-192-2608-01
    ("approved", False, 0),   # 未收货的 approved PO 绝不能被催付款
    ("issued", True, 1),      # 既有行为不变
])
async def test_backfill_admits_received_approved_po(po_status, has_gr, expect):
    """读端自愈 `_backfill_create_pa_tasks` 的状态白名单必须容纳「已收货的
    approved PO」,但只在真有 GR 时。

    服务/项目 PO 从 approved 起就能建 GR(api/v1/gr.py 的服务分支),而服务履约
    往往不会去点 place_order,所以 PO 会一直停在 approved。原白名单只有
    issued/partially_received/fully_received,于是这种单据既拿不到实时任务、也
    永远等不到自愈。
    """
    from app.crud.task import _backfill_create_pa_tasks
    from app.models.gr import GoodsReceipt

    async with sm.AsyncSessionLocal() as db:
        po, po_line, v, req = await _setup_po(db, po_type=4, po_status=po_status)
        gr_id = None
        if has_gr:
            gr = GoodsReceipt(number=f"GR-x{uuid.uuid4().hex[:6]}", title="G", po_id=po.id,
                              po_number=po.number, vendor_id=v.id, vendor_name="Acme",
                              gr_type="service", procurement_type=4, status="confirmed",
                              created_by=req.id)
            db.add(gr)
            await db.flush()
            gr_id = gr.id
        inv = await _match_invoice_header_only(db, po, v, req)
        inv.gr_id = gr_id          # 3-way 证据(无 GR 的对照组保持 None)
        await db.flush()

        await _backfill_create_pa_tasks(db)
        await db.flush()

        assert len(await _open_tasks(db, po.id, "create_pa")) == expect
