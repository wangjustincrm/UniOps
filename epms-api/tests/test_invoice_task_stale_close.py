"""发票类任务(match_invoice / review_match / resolve_exception)的收口。

起因(2026-09-09 生产审计,直接查库还原):Task Inbox 里挂着 4 条谁也做不了的
"Match Invoice to PO",最老的从 2026-08-13 起。以 INV-2026-0342 为例:

    17:13:30  AP 匹配 → 变差 197.08 vs 171.52 超容差 → 发票落 exception,
              建出 resolve_exception 任务
    17:13:46  同一个 AP 把这张票**指派**给别人重新匹配(assign_match 允许
              exception 状态)→ 建出 match_invoice 任务
    18:24:10  另一个人在异常面板点 Accept → 发票变 matched,
              resolve_exception 任务关闭
    此后 13 天  那条 match_invoice 的 updated_at 一次都没变过

POST /invoices/{id}/exception 只关它自己那条任务,而 match_invoice 又是全仓
唯一没有任何读端自愈的任务族 —— 于是永久挂着,而且发票已经 matched,
POST /match 会 409,受理人连"做掉它"这条路都没有。

两层修法各自独立可用,这里分开测:
  A. 事件层 —— resolve_exception 接受异常时一并关掉 match 任务并跑
     _on_invoice_matched(下游 create_pa / confirm_receipt 本来也没人建)。
  B. 读端层 —— crud.task._complete_stale_status_tasks 按单据状态兜底(声明表 TASK_LIVENESS),
     覆盖 rematch_from_existing 这类"在 crud 里改状态、根本不经过端点"的路径。

外加一条独立的 NULL 盲区:decline_match 会把任务弹回 ap_clerk 角色池
(assigned_user_id=NULL),而 match 端点原来用 `assigned_user_id != caller_id`
去关"别人的"任务 —— SQL 的 `<>` 对 NULL 恒为 NULL,那条任务两边都匹配不上。
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.db.session as session_module
from app.models.task import Task

from tests.test_invoice_allocations import INV_URL, _make_issued_po, _make_vendor
from tests.test_invoice_assign import _make_invoice, _make_user


# ── helpers ───────────────────────────────────────────────────────────────────

async def _tasks(invoice_id, task_type, *, open_only=True):
    async with session_module.AsyncSessionLocal() as db:
        stmt = select(Task).where(
            Task.type == task_type,
            Task.document_type == "invoice",
            Task.document_id == uuid.UUID(str(invoice_id)),
        )
        if open_only:
            stmt = stmt.where(Task.is_completed.is_(False))
        return (await db.execute(stmt)).scalars().all()


async def _over_tolerance_invoice(client, tag):
    """一张 200 对 PO 100 的发票 —— 变差 +100%,远超默认 5% 容差。
    match 之后必落 exception。返回 (invoice, po)。"""
    v = await _make_vendor(client, f"VND-{tag}")
    po = await _make_issued_po(client, v["id"], [
        {"description": "A", "qty": "1", "unit": "EA", "unit_price": "100.00"}])
    inv = await _make_invoice(client, v["id"], number=f"{tag}-0001", amount="200.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "200.00", "line_total": "200.00"}])
    r = await client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
         "po_line_id": po["line_items"][0]["id"],
         "allocated_amount": "200.00", "allocated_tax": "0.00"}]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "exception", r.json()
    return inv, po


# ── A. 事件层:accept 异常必须收掉指派任务 ────────────────────────────────────

@pytest.mark.asyncio
async def test_accepting_exception_closes_the_assignees_match_task(admin_client):
    """生产 INV-2026-0342 的完整复现:异常 → 指派 → 接受异常。

    这是修复前会留下僵尸的那条路径,断言的是"受理人手里不再有做不了的活"。
    """
    inv, _po = await _over_tolerance_invoice(admin_client, "STALE1")
    assignee = await _make_user("requester")

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                                json={"user_id": str(assignee)})
    assert r.status_code == 200, r.text
    assert len(await _tasks(inv["id"], "match_invoice")) == 1, "前提:指派确实建了任务"

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/exception",
                                json={"resolution": "accepted", "note": "variance ok"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched"

    assert await _tasks(inv["id"], "match_invoice") == [], (
        "发票已 matched、POST /match 会 409 —— 受理人不能还挂着一条匹配任务")
    closed = await _tasks(inv["id"], "match_invoice", open_only=False)
    assert len(closed) == 1 and closed[0].is_completed is True
    assert closed[0].completed_by is not None, "是人的动作关的,不是系统扫的"


@pytest.mark.asyncio
async def test_rejecting_exception_keeps_the_match_task_open(admin_client):
    """反向不变量:非 accepted 的处置不把发票推到 matched,
    受理人那条任务就还该开着 —— 否则这个修复会顺手删掉真实待办。"""
    inv, _po = await _over_tolerance_invoice(admin_client, "STALE2")
    assignee = await _make_user("requester")
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                            json={"user_id": str(assignee)})

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/exception",
                                json={"resolution": "credit_note_requested",
                                      "note": "sent back"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "exception", "只有 accepted 才落 matched"

    assert len(await _tasks(inv["id"], "match_invoice")) == 1, (
        "发票还在 exception,匹配任务仍然是真实待办")


@pytest.mark.asyncio
async def test_accepting_exception_raises_the_downstream_prompt(admin_client):
    """accept 之后发票就是 matched,下游该有落点。

    修复前 resolve_exception 从不调 _on_invoice_matched,所以这张 PO 既没有
    create_pa 也没有 confirm_receipt —— 生产那 4 条恰好被 GR 创建路径
    (crud.gr._on_three_way_reached)兜住了,但那只对"接受之后才收货"的 PO 成立。
    这里的 PO 没有 GR,走的是催收货分支。
    """
    inv, po = await _over_tolerance_invoice(admin_client, "STALE3")

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/exception",
                                json={"resolution": "accepted", "note": "ok"})
    assert r.status_code == 200, r.text

    async with session_module.AsyncSessionLocal() as db:
        rows = (await db.execute(select(Task).where(
            Task.document_type == "po",
            Task.document_id == uuid.UUID(po["id"]),
            Task.is_completed.is_(False),
        ))).scalars().all()
    assert {t.type for t in rows} & {"create_pa", "confirm_receipt"}, (
        f"accept 之后下游应有落点,实际只有 {[t.type for t in rows]}")


# ── B. 读端层:按发票状态兜底 ────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("task_type,live_status,dead_status", [
    ("match_invoice", "unmatched", "matched"),
    ("review_match", "match_review", "matched"),
    ("resolve_exception", "exception", "matched"),
])
async def test_sweep_closes_only_tasks_whose_invoice_moved_on(
    admin_client, task_type, live_status, dead_status,
):
    """三种任务各自只在一种发票状态下可做;发票离开那个状态,任务就是死的。

    直接建任务行再改发票状态,是为了覆盖"根本不经过端点"的漂移
    (PATCH /invoices/{id} → rematch_from_existing → match() 就是这种)。
    """
    from app.crud.task import _complete_stale_status_tasks
    from app.models.invoice import Invoice

    v = await _make_vendor(admin_client, f"VND-SWEEP-{task_type[:6]}")
    inv = await _make_invoice(admin_client, v["id"], number=f"SWP-{task_type[:6]}")
    inv_id = uuid.UUID(inv["id"])

    async with session_module.AsyncSessionLocal() as db:
        row = await db.get(Invoice, inv_id)
        row.status = live_status
        db.add(Task(type=task_type, priority="normal", document_type="invoice",
                    document_id=inv_id, document_number=inv["internal_ref"],
                    assigned_role="ap_clerk", title="t"))
        await db.commit()

    # 还在可做状态 → 一个都不能关
    async with session_module.AsyncSessionLocal() as db:
        await _complete_stale_status_tasks(db)
        await db.commit()
    assert len(await _tasks(inv_id, task_type)) == 1, (
        f"发票还在 {live_status},这条任务是真实待办")

    # 发票走掉 → 必须收口
    async with session_module.AsyncSessionLocal() as db:
        (await db.get(Invoice, inv_id)).status = dead_status
        await db.commit()
    async with session_module.AsyncSessionLocal() as db:
        await _complete_stale_status_tasks(db)
        await db.commit()

    assert await _tasks(inv_id, task_type) == []
    closed = await _tasks(inv_id, task_type, open_only=False)
    assert closed[0].completed_by is None, (
        "系统扫的必须留 NULL —— _already_surfaced 靠 completed_by 区分"
        "「用户主动驳回」和「系统收口」")


@pytest.mark.asyncio
async def test_inbox_read_heals_a_stranded_match_task(admin_client):
    """端到端:存量僵尸在下一次打开收件箱时自己消失,不需要清理脚本。

    生产那 4 条就是靠这条路收掉的。
    """
    from app.crud.task import get_for_role
    from app.models.invoice import Invoice

    inv, _po = await _over_tolerance_invoice(admin_client, "STALE4")
    assignee = await _make_user("requester")
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                            json={"user_id": str(assignee)})

    # 绕过端点、手工把发票推到 matched —— 模拟修复前留下的存量行
    async with session_module.AsyncSessionLocal() as db:
        (await db.get(Invoice, uuid.UUID(inv["id"]))).status = "matched"
        await db.commit()
    assert len(await _tasks(inv["id"], "match_invoice")) == 1, "前提:僵尸还在"

    async with session_module.AsyncSessionLocal() as db:
        items = await get_for_role(db, role="requester", user_id=assignee)
        await db.commit()

    assert not [t for t in items if t.type == "match_invoice"], "收件箱不该再列出它"
    assert await _tasks(inv["id"], "match_invoice") == [], "而且必须落库关掉"


# ── C. NULL 受理人:match 时的 `<>` 盲区 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_match_closes_a_task_bounced_back_to_the_ap_pool(admin_client):
    """decline_match 会把任务弹回 ap_clerk 角色池(assigned_user_id=NULL)。

    修复前 match 端点用 `assigned_user_id != caller_id` 找"别人的"任务,
    SQL 的 `<>` 对 NULL 恒为 NULL —— 这条任务既不是"我的"也不算"别人的",
    match 完照样开着,而且是广播给全体 ap_clerk 的。
    """
    v = await _make_vendor(admin_client, "VND-NULLASSIGNEE")
    po = await _make_issued_po(admin_client, v["id"], [
        {"description": "A", "qty": "1", "unit": "EA", "unit_price": "100.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="NUL-0001", amount="100.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "100.00", "line_total": "100.00"}])
    inv_id = uuid.UUID(inv["id"])

    async with session_module.AsyncSessionLocal() as db:
        db.add(Task(type="match_invoice", priority="normal", document_type="invoice",
                    document_id=inv_id, document_number=inv["internal_ref"],
                    assigned_role="ap_clerk", assigned_user_id=None,
                    title="Match invoice (assignment declined)"))
        await db.commit()

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
         "po_line_id": po["line_items"][0]["id"],
         "allocated_amount": "100.00", "allocated_tax": "0.00"}]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched"

    assert await _tasks(inv_id, "match_invoice") == [], (
        "角色池那条任务也必须被这次 match 关掉")


# ── D. GR 任务:Data Maintenance 改状态绕过 action() 的那道门 ──────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("task_type,live_status", [
    ("acknowledge_gr", "pending_ack"),
    ("collect_goods", "collection_pending"),
    ("confirm_service_gr", "collection_pending"),
])
async def test_sweep_closes_gr_tasks_after_a_status_edit(
    admin_client, task_type, live_status,
):
    """生产 GR-20260903-0015 的复现:有人在 DM 里把 GR 从 collection_pending
    直接改成 confirmed,`collect_goods` 任务就这么开着挂了 6 天。

    crud.gr.action() 每次跑都会关掉该 GR 上所有开放任务,所以这类任务只有在
    "状态变了但没走 action()"时才会漂 —— DM 的 status 编辑正是那道门
    (registry.py 声明它可编辑,admin/service.py 裸 setattr,零钩子)。
    那次编辑还留下了 action() 根本产生不出来的指纹:status='confirmed' 而
    collected_at 为 NULL。
    """
    from app.crud.task import _complete_stale_status_tasks
    from app.models.gr import GoodsReceipt

    v = await _make_vendor(admin_client, f"VND-GRSWP-{task_type[:5]}")
    po = await _make_issued_po(admin_client, v["id"], [
        {"description": "A", "qty": "1", "unit": "EA", "unit_price": "100.00"}])
    creator = await _make_user("warehouse_staff")
    gr_id = uuid.uuid4()
    async with session_module.AsyncSessionLocal() as db:
        db.add(GoodsReceipt(
            id=gr_id, number=f"GR-SWEEP-{gr_id.hex[:6]}", title="G",
            po_id=uuid.UUID(po["id"]), po_number=po["number"],
            vendor_id=uuid.UUID(v["id"]), vendor_name="V",
            gr_type="physical", procurement_type=2,
            status=live_status, created_by=creator))
        db.add(Task(type=task_type, priority="normal", document_type="gr",
                    document_id=gr_id, document_number=f"GR-SWEEP-{gr_id.hex[:6]}",
                    assigned_role="requester", title="t"))
        await db.commit()

    # 还在可做状态 → 不许动
    async with session_module.AsyncSessionLocal() as db:
        await _complete_stale_status_tasks(db)
        await db.commit()
    async with session_module.AsyncSessionLocal() as db:
        open_now = (await db.execute(select(Task).where(
            Task.document_id == gr_id, Task.is_completed.is_(False)))).scalars().all()
    assert len(open_now) == 1, f"GR 还在 {live_status},这条任务是真实待办"

    # DM 把状态改走 → 必须收口
    async with session_module.AsyncSessionLocal() as db:
        (await db.get(GoodsReceipt, gr_id)).status = "confirmed"
        await db.commit()
    async with session_module.AsyncSessionLocal() as db:
        await _complete_stale_status_tasks(db)
        await db.commit()

    async with session_module.AsyncSessionLocal() as db:
        rows = (await db.execute(select(Task).where(
            Task.document_id == gr_id))).scalars().all()
    assert rows[0].is_completed is True
    assert rows[0].completed_by is None, "系统收口必须留 NULL"


# ── E. revise_* / process_pa:重新提交后没人关 ─────────────────────────────────

@pytest.mark.asyncio
async def test_resubmitting_a_returned_pa_leaves_no_revise_task(admin_client):
    """生产 PA-20260831-0002 的复现。

    approval-api 的 execute_action 里,approve / return / reject 三个分支都调
    _complete_tasks,**只有 submit 分支没调**(engine.py 的 `if act == "submit"`)。
    于是"退回 → 申请人改完重新提交"之后,那条 revise_pa 还挂在申请人收件箱里,
    而 submitted 状态的 PA 他根本编辑不了。

    这里直接摆出 submit 之后的终局状态(PA 回到 submitted、revise 任务还开着),
    断言读端会把它收掉 —— 不依赖 approval-api 起没起。
    """
    from app.crud.task import _complete_stale_status_tasks
    from app.models.pa import PaymentApplication

    v = await _make_vendor(admin_client, f"VND-REVPA-{uuid.uuid4().hex[:5]}")
    pa_id = uuid.uuid4()
    requester = await _make_user("requester")
    async with session_module.AsyncSessionLocal() as db:
        db.add(PaymentApplication(
            id=pa_id, pa_number=f"PA-REV-{pa_id.hex[:6]}", title="T",
            vendor_id=uuid.UUID(v["id"]), vendor_name="V", currency="CAD",
            subtotal=Decimal("100"), tax_amount=Decimal("0"),
            payment_amount=Decimal("100"), status="returned",
            created_by=requester))
        db.add(Task(type="revise_pa", priority="normal", document_type="pa",
                    document_id=pa_id, document_number=f"PA-REV-{pa_id.hex[:6]}",
                    assigned_role="requester", assigned_user_id=requester,
                    title="Revise PA"))
        await db.commit()

    # 还在 returned → 申请人确实要改,不许关
    async with session_module.AsyncSessionLocal() as db:
        await _complete_stale_status_tasks(db)
        await db.commit()
    async with session_module.AsyncSessionLocal() as db:
        assert (await db.execute(select(Task).where(
            Task.document_id == pa_id, Task.is_completed.is_(False)
        ))).scalars().all(), "returned 的 PA 上,revise 任务是真实待办"

    # 重新提交 → 收口
    async with session_module.AsyncSessionLocal() as db:
        (await db.get(PaymentApplication, pa_id)).status = "submitted"
        await db.commit()
    async with session_module.AsyncSessionLocal() as db:
        await _complete_stale_status_tasks(db)
        await db.commit()
    async with session_module.AsyncSessionLocal() as db:
        rows = (await db.execute(select(Task).where(Task.document_id == pa_id))).scalars().all()
    assert rows[0].is_completed is True, "PA 已重新提交,revise 任务必须收掉"
    assert rows[0].completed_by is None


@pytest.mark.asyncio
@pytest.mark.parametrize("doc_type", ["pa", "pa_dir"])
async def test_process_pa_closes_once_the_pa_leaves_approved(admin_client, doc_type):
    """process_pa 只在 PA approved 且未付时可做。

    finance-api 的付款执行会把 status 置成 processed 并在同一事务里关掉这条任务
    (crud/payment_execute.py 的 _complete_open_tasks,pa 和 pa_dir 都覆盖)。
    走到别的状态还开着 —— cancelled、returned、或 DM 手改状态 —— 这笔付款就
    不可能发生了。pa_dir 单列一份:同一张表,不同 document_type,原来两个
    清扫都够不着它。
    """
    from app.crud.task import _complete_stale_status_tasks
    from app.models.pa import PaymentApplication

    v = await _make_vendor(admin_client, f"VND-PROC-{uuid.uuid4().hex[:5]}")
    pa_id = uuid.uuid4()
    async with session_module.AsyncSessionLocal() as db:
        db.add(PaymentApplication(
            id=pa_id, pa_number=f"PA-PROC-{pa_id.hex[:6]}", title="T",
            vendor_id=uuid.UUID(v["id"]), vendor_name="V", currency="CAD",
            subtotal=Decimal("100"), tax_amount=Decimal("0"),
            payment_amount=Decimal("100"), status="approved",
            created_by=await _make_user("ap_clerk")))
        db.add(Task(type="process_pa", priority="normal", document_type=doc_type,
                    document_id=pa_id, document_number=f"PA-PROC-{pa_id.hex[:6]}",
                    assigned_role="payment_officer", title="Process PA"))
        await db.commit()

    async with session_module.AsyncSessionLocal() as db:
        await _complete_stale_status_tasks(db)
        await db.commit()
    async with session_module.AsyncSessionLocal() as db:
        assert (await db.execute(select(Task).where(
            Task.document_id == pa_id, Task.is_completed.is_(False)
        ))).scalars().all(), "approved 且未付 —— 这是真实待付队列,不许动"

    async with session_module.AsyncSessionLocal() as db:
        (await db.get(PaymentApplication, pa_id)).status = "cancelled"
        await db.commit()
    async with session_module.AsyncSessionLocal() as db:
        await _complete_stale_status_tasks(db)
        await db.commit()
    async with session_module.AsyncSessionLocal() as db:
        rows = (await db.execute(select(Task).where(Task.document_id == pa_id))).scalars().all()
    assert rows[0].is_completed is True, f"{doc_type} 已 cancelled,process_pa 必须收掉"


@pytest.mark.asyncio
async def test_stale_approve_sweep_now_covers_direct_pas(admin_client):
    """approve_pa 的清扫原来只 join document_type='pa'。Direct(OA)PA 用
    document_type='pa_dir' 写同一张表,是 EPMS 自己表里唯一一个完全没有
    对账的审批族。"""
    from app.crud.task import _complete_stale_status_tasks
    from app.models.pa import PaymentApplication

    v = await _make_vendor(admin_client, f"VND-DIRPA-{uuid.uuid4().hex[:5]}")
    pa_id = uuid.uuid4()
    async with session_module.AsyncSessionLocal() as db:
        db.add(PaymentApplication(
            id=pa_id, pa_number=f"PA-DIR-{pa_id.hex[:6]}", title="T",
            vendor_id=uuid.UUID(v["id"]), vendor_name="V", currency="CAD",
            subtotal=Decimal("100"), tax_amount=Decimal("0"),
            payment_amount=Decimal("100"), status="cancelled",
            created_by=await _make_user("ap_clerk")))
        db.add(Task(type="approve_pa", priority="normal", document_type="pa_dir",
                    document_id=pa_id, document_number=f"PA-DIR-{pa_id.hex[:6]}",
                    assigned_role="dept_manager", title="Approve Direct PA"))
        await db.commit()

    async with session_module.AsyncSessionLocal() as db:
        await _complete_stale_status_tasks(db)
        await db.commit()
    async with session_module.AsyncSessionLocal() as db:
        rows = (await db.execute(select(Task).where(Task.document_id == pa_id))).scalars().all()
    assert rows[0].is_completed is True, "已取消的 Direct PA 不该还挂着审批任务"


# ── F. PATCH /invoices/{id}:编辑也能让发票 matched ───────────────────────────

@pytest.mark.asyncio
async def test_editing_an_invoice_into_matched_raises_the_downstream_prompt(admin_client):
    """rematch_from_existing 直接调 crud.match(),而"发票刚变 matched"的全部后续
    处理都写在 API 层(match / match-review 两个端点)—— 编辑这条路一样都没跑到。

    可达性说明:PATCH 改不了分摊行,所以光改金额只会把发票打回 unmatched
    (分摊不平),推不到 matched。真正可达的跃迁是**容差被调宽**之后再编辑一次:
    rematch_from_existing 会用**新**容差重跑 match,原来超差的那张就落回 matched。
    这是一次配置改动 + 一次编辑,不是理论路径。
    """
    from app.models.config import CompanyConfig

    inv, po = await _over_tolerance_invoice(admin_client, "EDIT1")

    # 容差从默认放宽到 200% —— 200 对 100 的变差现在在容差内
    async with session_module.AsyncSessionLocal() as db:
        cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
        assert cfg is not None, "前提:测试库里有一行 company_config"
        cfg.invoice_match_tolerance_pct = Decimal("200")
        await db.commit()

    r = await admin_client.patch(f"{INV_URL}/{inv['id']}", json={"notes": "re-checked"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched", r.json()["status"]

    async with session_module.AsyncSessionLocal() as db:
        rows = (await db.execute(select(Task).where(
            Task.document_type == "po", Task.document_id == uuid.UUID(po["id"]),
            Task.is_completed.is_(False)))).scalars().all()
    assert {t.type for t in rows} & {"create_pa", "confirm_receipt"}, (
        f"编辑成 matched 之后下游应有落点,实际只有 {[t.type for t in rows]}")

    async with session_module.AsyncSessionLocal() as db:
        cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
        cfg.invoice_match_tolerance_pct = Decimal("0")
        await db.commit()


@pytest.mark.asyncio
async def test_editing_an_already_matched_invoice_does_not_renotify(admin_client):
    """反向不变量:门禁挂在**状态跃迁**上,不是状态本身。

    _create_or_renotify_create_pa 只要发现已有开放任务就**重发**那封"请建付款
    申请"的邮件。挂在状态上的话,一张 matched 发票每改一个字都会再骚扰申请人一次。
    断言第二次编辑不新增、不重开任何 PO 任务。

    用一张容差内的干净 matched 发票,而不是"accept 过异常"的那种 —— 后者一编辑
    就会被 rematch_from_existing 用当前容差重算、打回 exception(accept 的决定
    被编辑抹掉,这是另一个独立问题,不在本测试范围)。
    """
    v = await _make_vendor(admin_client, "VND-EDIT2")
    po = await _make_issued_po(admin_client, v["id"], [
        {"description": "A", "qty": "1", "unit": "EA", "unit_price": "100.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="EDIT2-0001", amount="100.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "100.00", "line_total": "100.00"}])
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
         "po_line_id": po["line_items"][0]["id"],
         "allocated_amount": "100.00", "allocated_tax": "0.00"}]})
    assert r.status_code == 200 and r.json()["status"] == "matched", r.text

    async def _po_task_ids():
        async with session_module.AsyncSessionLocal() as db:
            return sorted(str(t.id) for t in (await db.execute(select(Task).where(
                Task.document_type == "po", Task.document_id == uuid.UUID(po["id"]),
                Task.is_completed.is_(False)))).scalars().all())

    before = await _po_task_ids()
    assert before, "前提:第一次 match 已经签出了下游任务"

    r = await admin_client.patch(f"{INV_URL}/{inv['id']}", json={"notes": "typo fix"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched"
    assert await _po_task_ids() == before, "已经是 matched 的发票,编辑不该再动下游任务"
