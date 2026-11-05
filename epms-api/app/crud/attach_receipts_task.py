"""attach_receipts —— 把「给 house_account 发票挂签收凭证」派给协议责任人。

为什么要有它(用户 2026-10-20):AP 上传发票后直接 match 到 house_account 协议,
发票就成了 matched;assign-match 只认 unmatched/exception,于是再也没有入口把
「挂凭证」这件事交给协议 owner。owner 自己上传了 Delivery note,打开发票却
403 —— 挂凭证的闸门只放行 AP / 上传人 / match_invoice 任务持有人。生产
2026-10-20 实测 12 张 matched、零凭证、零 PA 的 house_account 发票就卡在这里。

不变量(needs_receipt_evidence):发票 match 到 **house_account** 协议、状态还是
matched/approved、没挂凭证(receipt_ids 为空)、也没声明无凭证结算
(legacy_settlement=False)、并且还没有活着的 PA 列着它 —— 有 PA 时
set_receipts/settle_without_receipt 都会拒绝,留着任务就是一条做不了的僵尸。

收口全靠这一个判定,两个调用点:
  * sync_for_invoice —— 每个会改动上面任一条件的端点(match、match-review、
    PATCH、resolve exception、挂凭证、无凭证结算、删除)在提交前调用;
  * sweep —— 跑在 Task Inbox 读端(crud.task.get_for_role 的 advisory lock 里),
    补建存量、关掉被 Data Maintenance 之类绕开端点改掉的。
关闭时 completed_by 留 NULL(系统收口);用户自己 Mark Done 的视为永久驳回,
不再补建 —— 与 crud.task._already_surfaced 同口径。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, and_, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import PurchaseAgreement
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.task import Task

TASK_TYPE = "attach_receipts"
_LIVE_INVOICE_STATUSES = ("matched", "approved")


def _no_evidence_clause():
    """SQL 版的 needs_receipt_evidence(不含协议类型,由调用方 join 协议后加)。
    PA→发票只有 invoice_ids 这个 JSONB 数组(无外键),与
    crud.invoice._invoice_referenced_by_active_pa 同口径用 @> 判包含。"""
    live_pa = exists().where(
        PaymentApplication.invoice_ids.op("@>")(
            func.jsonb_build_array(cast(Invoice.id, String))),
        PaymentApplication.status != "cancelled",
    )
    return and_(
        Invoice.status.in_(_LIVE_INVOICE_STATUSES),
        # receipt_ids 可能是 SQL NULL、JSON null(ORM 写 None 时)或数组 ——
        # 对非数组调 jsonb_array_length 会直接报错,先判类型。
        or_(func.coalesce(func.jsonb_typeof(Invoice.receipt_ids), "null") != "array",
            func.jsonb_array_length(Invoice.receipt_ids) == 0),
        Invoice.legacy_settlement.is_(False),
        ~live_pa,
    )


async def needs_receipt_evidence(db: AsyncSession, inv: Invoice) -> PurchaseAgreement | None:
    """Return the house_account agreement this invoice still owes receipt
    evidence to, or None when no attach_receipts task should be open."""
    if inv.agreement_id is None or inv.status not in _LIVE_INVOICE_STATUSES:
        return None
    if inv.receipt_ids or inv.legacy_settlement:
        return None
    agr = (await db.execute(
        select(PurchaseAgreement).where(PurchaseAgreement.id == inv.agreement_id)
    )).scalar_one_or_none()
    if agr is None or agr.agreement_type != "house_account":
        return None
    from app.crud.invoice import _invoice_referenced_by_active_pa
    if await _invoice_referenced_by_active_pa(db, inv.id) is not None:
        return None
    return agr


async def _assignee(db: AsyncSession, agr: PurchaseAgreement) -> tuple[uuid.UUID | None, str]:
    """协议 owner → 部门在职经理(与 confirm_period 同一个口径,_confirm_assignee)
    → 都没有就回落 AP 角色池。不学 confirm_period 退到 system_admin:挂凭证本来
    就是 AP 能做的事,AP 池是真能办的人,而 dept_manager 广播会漏进全公司经理。"""
    from app.crud.agreement_schedule import _confirm_assignee
    user_id, role = await _confirm_assignee(db, agr)
    if user_id is None:
        return None, "ap_clerk"
    return user_id, role or "dept_manager"


def _build_task(inv: Invoice, agr: PurchaseAgreement,
                assignee_id: uuid.UUID | None, role: str) -> Task:
    return Task(
        type="attach_receipts",   # 字面量:tests/test_task_liveness_registry.py 的 AST 守卫只认这个
        priority="normal",
        document_type="invoice",
        document_id=inv.id,
        document_number=inv.internal_ref,
        assigned_role=role,
        assigned_user_id=assignee_id,
        title=f"Attach receipts to invoice {inv.internal_ref} — {agr.number}",
        description=(
            f"Invoice {inv.internal_ref} has been matched to agreement {agr.number} "
            f"({agr.title}) but has no receipt evidence yet. Open the invoice's "
            "3-Way Match tab and tick the receipts / delivery notes it covers — or, "
            "if there are none, settle it without receipt evidence and give the "
            "reason. A Payment Application cannot be raised until one of the two "
            "is done. Record any missing delivery notes on the agreement first."
        ),
        vendor=inv.vendor_name,
        amount=inv.total_amount,
    )


async def _open_tasks(db: AsyncSession, invoice_id: uuid.UUID) -> list[Task]:
    return list((await db.execute(select(Task).where(
        Task.type == TASK_TYPE, Task.document_type == "invoice",
        Task.document_id == invoice_id, Task.is_completed.is_(False),
    ))).scalars().all())


async def _user_dismissed(db: AsyncSession, invoice_id: uuid.UUID) -> bool:
    return (await db.execute(select(Task.id).where(
        Task.type == TASK_TYPE, Task.document_type == "invoice",
        Task.document_id == invoice_id, Task.is_completed.is_(True),
        Task.completed_by.is_not(None),
    ).limit(1))).first() is not None


def _close(tasks: list[Task]) -> None:
    now = datetime.now(timezone.utc)
    for t in tasks:
        t.is_completed = True
        t.completed_at = now


async def sync_for_invoice(db: AsyncSession, inv: Invoice) -> None:
    """Open or close this invoice's attach_receipts task to match its state.

    Idempotent — call it after any change to the invoice's status, agreement
    link or receipt evidence. Rides on the caller's session (flushes, never
    commits)."""
    open_tasks = await _open_tasks(db, inv.id)
    agr = await needs_receipt_evidence(db, inv)
    if agr is None:
        if open_tasks:
            _close(open_tasks)
            await db.flush()
        return
    if open_tasks or await _user_dismissed(db, inv.id):
        return
    assignee_id, role = await _assignee(db, agr)
    db.add(_build_task(inv, agr, assignee_id, role))
    await db.flush()


async def close_for_invoice(db: AsyncSession, invoice_id: uuid.UUID) -> None:
    """Close unconditionally — the invoice is about to be deleted."""
    tasks = await _open_tasks(db, invoice_id)
    if tasks:
        _close(tasks)
        await db.flush()


async def reassign_for_agreement(db: AsyncSession, agr: PurchaseAgreement) -> int:
    """协议 owner 变了:把还开着的挂凭证任务改派给新责任人(同
    agreement_schedule.reassign_open_confirm_tasks 的理由 —— 任务是在 match 那一刻
    解析责任人的,之后改 owner 不会回头动它)。"""
    tasks = (await db.execute(
        select(Task).join(Invoice, Invoice.id == Task.document_id).where(
            Task.type == TASK_TYPE, Task.document_type == "invoice",
            Task.is_completed.is_(False), Invoice.agreement_id == agr.id,
        )
    )).scalars().all()
    if not tasks:
        return 0
    assignee_id, role = await _assignee(db, agr)
    changed = 0
    for t in tasks:
        if t.assigned_user_id == assignee_id and t.assigned_role == role:
            continue
        t.assigned_user_id = assignee_id
        t.assigned_role = role
        changed += 1
    if changed:
        await db.flush()
    return changed


async def sweep(db: AsyncSession) -> None:
    """Task Inbox 读端兜底:关掉不再需要的,补建漏掉的(存量 + 绕开端点的改动)。"""
    still_needed = (
        select(Invoice.id)
        .join(PurchaseAgreement, PurchaseAgreement.id == Invoice.agreement_id)
        .where(PurchaseAgreement.agreement_type == "house_account", _no_evidence_clause())
    )
    stale = (await db.execute(select(Task).where(
        Task.type == TASK_TYPE, Task.document_type == "invoice",
        Task.is_completed.is_(False),
        Task.document_id.not_in(still_needed),
    ))).scalars().all()
    _close(list(stale))

    surfaced = select(Task.document_id).where(
        Task.type == TASK_TYPE, Task.document_type == "invoice",
        or_(Task.is_completed.is_(False), Task.completed_by.is_not(None)),
    )
    rows = (await db.execute(
        select(Invoice, PurchaseAgreement)
        .join(PurchaseAgreement, PurchaseAgreement.id == Invoice.agreement_id)
        .where(PurchaseAgreement.agreement_type == "house_account",
               _no_evidence_clause(),
               Invoice.id.not_in(surfaced))
    )).all()
    assignees: dict[uuid.UUID, tuple[uuid.UUID | None, str]] = {}
    for inv, agr in rows:
        if agr.id not in assignees:
            assignees[agr.id] = await _assignee(db, agr)
        assignee_id, role = assignees[agr.id]
        db.add(_build_task(inv, agr, assignee_id, role))
    if stale or rows:
        await db.flush()
