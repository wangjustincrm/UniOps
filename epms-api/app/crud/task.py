"""CRUD for Task inbox."""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import PurchaseAgreement
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.crud.pa_links import po_ids_with_pa
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task

# Import-reconstruction backfills only synthesize follow-on tasks for reasonably
# recent documents. PMS carries years of history; without a floor the backfills
# would resurrect ancient imported docs as live inbox work. 2026-06-01 is the
# cutoff (roughly when EPMS became the system of record); every month from then
# on is included, so this stays correct as time moves forward.
_BACKFILL_MIN_CREATED = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _already_surfaced(task_type: str, document_type: str):
    """Docs whose backfilled task must NOT be re-raised — those with either an
    OPEN task OR one a USER explicitly dismissed via "Mark Done"
    (completed_by IS NOT NULL).

    Why completed_by matters (prod bug): "Mark Done" (POST /tasks/{id}/complete)
    only flips is_completed; it does not advance the underlying document. A
    place_order task's PO stays 'approved'/unplaced, so a backfill that keyed
    dedup on OPEN tasks alone re-raised it every inbox load — and because the
    inbox fires several GET /tasks at once, the unguarded re-raise landed as
    DUPLICATE open rows. Treating a user completion as permanent dismissal fixes
    both: the task stays gone, and there is nothing left to duplicate.

    System/document completions (completed_by IS NULL — _complete_tasks and the
    stale-sweeps) are deliberately NOT counted here, so a legitimate re-raise
    still fires (e.g. a PR whose PO was deleted must re-prompt Create PO)."""
    return select(Task.document_id).where(
        Task.type == task_type,
        Task.document_type == document_type,
        or_(Task.is_completed.is_(False), Task.completed_by.is_not(None)),
    )


async def _complete_stale_create_pa_tasks(db: AsyncSession) -> None:
    """Auto-complete open create_pa tasks once a PA already exists for the PO.

    Mirrors _complete_stale_create_po_tasks. Covers both task anchors:
      - document_type="po" → PA exists with po_id == document_id
      - document_type="gr" → the GR's PO has a PA
    Needed both as a safety net and to clear tasks created before PA creation
    started completing them.
    """
    pos_with_pa = po_ids_with_pa()
    grs_whose_po_has_pa = (
        select(GoodsReceipt.id).where(GoodsReceipt.po_id.in_(pos_with_pa))
    )
    stale_q = select(Task).where(
        Task.type == "create_pa",
        Task.is_completed.is_(False),
        or_(
            and_(Task.document_type == "po", Task.document_id.in_(pos_with_pa)),
            and_(Task.document_type == "gr", Task.document_id.in_(grs_whose_po_has_pa)),
        ),
    )
    result = await db.execute(stale_q)
    now = datetime.now(timezone.utc)
    for task in result.scalars().all():
        task.is_completed = True
        task.completed_at = now
    await db.flush()


async def _complete_orphan_create_pa_tasks(
    db: AsyncSession, po_id: uuid.UUID | None = None
) -> None:
    """Complete open PO-anchored create_pa tasks whose PO has NO matched invoice
    and NO PA — the matched invoice that raised the task was later deleted or
    reverted out of "matched". Without this the task lingers forever, because
    completion is otherwise tied only to PA creation (see
    _complete_stale_create_pa_tasks) — a real prod orphan (POs with no invoice
    still prompting "Create Payment Application").

    po_id=None → sweep every such orphan (inbox self-heal on each GET /tasks).
    po_id set  → target one PO, called the moment an invoice leaves "matched" /
                 is deleted so the requester's inbox clears immediately.

    Scoped to document_type="po": GR-anchored create_pa tasks legitimately have
    no invoice, so they are left to the PA-based completion path. A "matched"
    invoice counts whether it links via header po_id OR a line allocation — the
    same reference invoice_crud.list treats as "this PO has an invoice".
    """
    pos_with_pa = po_ids_with_pa()
    pos_matched_header = select(Invoice.po_id).where(
        Invoice.status == "matched", Invoice.po_id.is_not(None)
    )
    pos_matched_alloc = (
        select(InvoicePoAllocation.po_id)
        .join(Invoice, Invoice.id == InvoicePoAllocation.invoice_id)
        .where(Invoice.status == "matched")
    )
    q = select(Task).where(
        Task.type == "create_pa",
        Task.is_completed.is_(False),
        Task.document_type == "po",
        Task.document_id.not_in(pos_with_pa),
        Task.document_id.not_in(pos_matched_header),
        Task.document_id.not_in(pos_matched_alloc),
    )
    if po_id is not None:
        q = q.where(Task.document_id == po_id)
    tasks = (await db.execute(q)).scalars().all()
    if not tasks:
        return
    now = datetime.now(timezone.utc)
    for task in tasks:
        task.is_completed = True
        task.completed_at = now
    await db.flush()


async def _complete_stale_place_order_tasks(db: AsyncSession) -> None:
    """Complete open place_order tasks whose PO is no longer 'approved'.

    place_order is only valid while a PO sits 'approved' and unplaced. The live
    place_order() action flips the PO to 'issued' AND completes the task via
    _complete_tasks. PMS-imported / bulk-synced POs were set straight to
    issued / received WITHOUT that action, so their place_order tasks were never
    completed and linger in Procurement's inbox (real prod: 11 such tasks on
    issued / fully_received POs). Mirrors _backfill_place_order_tasks' inverse:
    it creates only for status == 'approved', so anything past that is stale.
    """
    pos_not_approved = select(PurchaseOrder.id).where(PurchaseOrder.status != "approved")
    stale_q = select(Task).where(
        Task.type == "place_order",
        Task.is_completed.is_(False),
        Task.document_type == "po",
        Task.document_id.in_(pos_not_approved),
    )
    tasks = (await db.execute(stale_q)).scalars().all()
    if not tasks:
        return
    now = datetime.now(timezone.utc)
    for task in tasks:
        task.is_completed = True
        task.completed_at = now
    await db.flush()


async def _complete_stale_create_prepayment_pa_tasks(db: AsyncSession) -> None:
    """Auto-complete open create_prepayment_pa tasks once a prepayment PA exists.

    The task (document_type="po", document_id=<po_id>) prompts the requester to
    raise an advance payment; a regular PA does NOT satisfy it, so only count PAs
    with pa_type="prepayment". Safety net + clears tasks created before PA
    creation started completing them.
    """
    pos_with_prepayment = po_ids_with_pa(PaymentApplication.pa_type == "prepayment")
    stale_q = select(Task).where(
        Task.type == "create_prepayment_pa",
        Task.is_completed.is_(False),
        Task.document_type == "po",
        Task.document_id.in_(pos_with_prepayment),
    )
    result = await db.execute(stale_q)
    now = datetime.now(timezone.utc)
    for task in result.scalars().all():
        task.is_completed = True
        task.completed_at = now
    await db.flush()


# ── When is a task still actionable? ─────────────────────────────────────────
#
# The `tasks` table is MATERIALIZED, not derived: nothing recomputes whether a
# row still represents work, so every status transition has to remember to close
# its own tasks by hand. The 2026-09-09 prod audit found three that don't —
# POST /invoices/{id}/exception (accept), approval-api's `submit` branch (the
# one action branch with no _complete_tasks call), and Data Maintenance's
# bare-setattr status edit — leaving six rows of work nobody could perform, the
# oldest open since 2026-07-22.
#
# This table fixes the CLASS rather than those three instances: one declaration
# per task type of the document statuses in which it is still actionable —
# almost always exactly the states its own endpoint accepts, so a task outside
# them is one the holder would get a 409 on. _complete_stale_status_tasks closes
# anything outside its set on every inbox read, which is also what clears the
# existing rows without a data script.
#
# EVERY task type the codebase can emit must be classified here or in
# _TASK_DEDICATED_HANDLING / _TASK_NO_STATUS_INVARIANT below.
# tests/test_task_liveness_registry.py scans app/ for emitted task types and
# fails when one is in none of the three, so the next task type cannot arrive
# without someone deciding what keeps it alive.


@dataclass(frozen=True)
class _Liveness:
    model: Any                       # ORM model carrying the .status column
    document_types: tuple[str, ...]  # Task.document_type values that anchor here
    live_statuses: tuple[str, ...]   # statuses in which the task is still work
    why: str


_APPROVABLE = ("submitted", "in_review")
# The approval engine's _DOC_META valid_submit — identical for every doc type.
_RESUBMITTABLE = ("draft", "returned")

TASK_LIVENESS: dict[str, _Liveness] = {
    # ── Invoice ──────────────────────────────────────────────────────────────
    "match_invoice": _Liveness(
        Invoice, ("invoice",), ("unmatched", "exception"),
        "POST /invoices/{id}/match 自己的状态闸门。accept 异常会把发票推到 matched "
        "而只关 resolve_exception —— 生产 INV-2026-0031/0342/0348/0379 就是这么僵的。"),
    "review_match": _Liveness(
        Invoice, ("invoice",), ("match_review",),
        "crud.review_match 对其它状态直接 raise。"),
    "resolve_exception": _Liveness(
        Invoice, ("invoice",), ("exception",),
        "crud.resolve_exception 对其它状态直接 raise。"),

    # ── Goods receipt ────────────────────────────────────────────────────────
    # crud.gr.action() 每次跑都关掉该 GR 上所有开放任务,所以这三类只在"状态变了
    # 却没走 action()"时漂 —— Data Maintenance 的 status 编辑正是那道门。生产
    # GR-20260903-0015 被那样改成 confirmed,collect_goods 挂了 6 天,至今带着
    # action() 产生不出来的指纹:confirmed 而 collected_at 为 NULL。
    "acknowledge_gr": _Liveness(
        GoodsReceipt, ("gr",), ("pending_ack",), "action('acknowledge') 的状态闸门。"),
    "collect_goods": _Liveness(
        GoodsReceipt, ("gr",), ("collection_pending",), "action('collect') 的状态闸门。"),
    "confirm_service_gr": _Liveness(
        GoodsReceipt, ("gr",), ("collection_pending",), "action('confirm') 的服务分支。"),

    # ── Approvals ────────────────────────────────────────────────────────────
    # 引擎在同一事务里关审批任务,但配置重同步 / 导入 / 绕过引擎的状态翻转会漂,
    # 留下点开就 409 的"幽灵待审"—— 它在 Task Inbox 里(只看 is_completed)却不在
    # Dashboard 上(还看单据状态),两个界面互相打架。与 approval-api 的
    # engine._resync_document 同口径。只动 approve_*,绝不碰已完成单据上合法的
    # 后续工作(place_order / create_pa)。
    "approve_pr": _Liveness(PurchaseRequest, ("pr",), _APPROVABLE, "引擎 valid_approve。"),
    "approve_po": _Liveness(PurchaseOrder, ("po",), _APPROVABLE, "引擎 valid_approve。"),
    "approve_pa": _Liveness(
        PaymentApplication, ("pa", "pa_dir"), _APPROVABLE,
        "引擎 valid_approve。★pa_dir(OA Direct PA)与 pa 同表不同 document_type;"
        "漏掉它时它是 EPMS 自有表里唯一完全没有对账的审批族。"),
    "approve_agr": _Liveness(
        PurchaseAgreement, ("agr",), _APPROVABLE,
        "协议离开可审集的路子比 PO 多一条:到期。漏掉它就重现 Inbox/Dashboard 打架。"),

    # ── Returned for revision ────────────────────────────────────────────────
    # ★引擎的 submit 分支是唯一不调 _complete_tasks 的动作分支(approve / return /
    # reject 都调),所以"退回 → 改完重交"之后 revise 任务还挂在申请人手里,而
    # submitted 状态的单据他根本编辑不了。生产 PA-20260831-0002。
    "revise_pr": _Liveness(PurchaseRequest, ("pr",), _RESUBMITTABLE, "引擎 valid_submit。"),
    "revise_po": _Liveness(PurchaseOrder, ("po",), _RESUBMITTABLE, "引擎 valid_submit。"),
    "revise_pa": _Liveness(
        PaymentApplication, ("pa", "pa_dir"), _RESUBMITTABLE, "引擎 valid_submit。"),
    "revise_agr": _Liveness(
        PurchaseAgreement, ("agr",), _RESUBMITTABLE, "引擎 valid_submit。"),

    # ── Payment execution ────────────────────────────────────────────────────
    "process_pa": _Liveness(
        PaymentApplication, ("pa", "pa_dir"), ("approved",),
        "PA 走完审批时签出,由 finance-api 的付款执行在同一事务里置 processed 并关掉"
        "(crud/payment_execute.py 的 _complete_open_tasks,pa 与 pa_dir 都覆盖)。"
        "其它状态还开着 —— cancelled / returned / DM 手改 —— 这笔付款就不可能发生了。"),
}

# 有专属收口逻辑,不变量不是"比一下状态"这么简单,所以不进上表。
_TASK_DEDICATED_HANDLING: dict[str, str] = {
    "create_po": "_complete_stale_create_po_tasks:看 PR 是否已有 PO,两个方向的外键都要看。",
    "place_order": "_complete_stale_place_order_tasks:PO 不再是 approved 即失效。",
    "create_pa": "_complete_stale_create_pa_tasks + _complete_orphan_create_pa_tasks:"
                 "既看 PA 在不在,也看 matched 发票还在不在,还跨 po/gr 两种锚点。",
    "create_prepayment_pa": "_complete_stale_create_prepayment_pa_tasks:只有 prepayment 型 PA 算数。",
    "confirm_receipt": "不变量是「PO 有收货证据」(crud.po.po_has_receipt_evidence:有活 GR "
                       "单据 或 任一行 received_qty>0),要 per-PO 子查询而不是比状态;"
                       "两个建单点与 GR 创建路径都已强制。★它由两条完全不同的路签出:"
                       "发票匹配(未收货)和服务 PO 完成日催办(tasks/service_gr_due.py,"
                       "与发票无关)—— 同 type 不同不变量,2026-09-09 审计时拿前者去套后者,"
                       "误判过 8 条。",
    "confirm_period": "接受期次时关(crud.agreement_schedule),释放协议证据时也关"
                      "(crud.invoice._release_agreement_evidence),两侧都有。",
    "chase_agreement_invoice": "tasks/agreement_overdue.py 的 _close_settled_chase_tasks:"
                               "协议不再有 overdue 期次即收口。★跑在定时循环里,"
                               "agreement_overdue_enabled 关掉时只出不进。",
}

# 本服务兜不住的,连同原因。
_TASK_NO_STATUS_INVARIANT: dict[str, str] = {
    "gr_damage_report": "「找人看一眼」的提示,不是收货状态机里的一步 —— 没有能判死的状态。",
    "confirm_settlement": "预付冲销确认,由 crud.pa 的结算流程关。",
    "sign_po": "posign 走自己的 step_attr,PO 的 status 不是它的闸门。",
    "revise_po_signoff": "同 sign_po。",
    "process_expense": "expense_claims 归 expense-api,epms-api 没有该 model。",
    "approve_budget_plan": "budget_plans 归 finance-api,epms-api 没有该 model。",
    "revise_budget_plan": "同 approve_budget_plan。",
    "approve_vms_visit": "vms_visits 归 vms-api,epms-api 没有该 model。",
    "revise_vms_visit": "同 approve_vms_visit。",
    "check_out_visitor": "vms-api 自己的 close_settled_visit_tasks 收口。",
    "prepare_ppe": "同 check_out_visitor。",
    "vms_confirm_training": "vms-api 的 services/compliance.py 收口。",
    "vms_confirm_ppe": "同 vms_confirm_training。",
    "import_erp_vendor": "NC 同步每趟自己对账(services/nc_purchase_sync/error_tasks.py)。",
    "resolve_nc_sync_error": "同 import_erp_vendor。",
}

# ★覆盖边界:上表只能覆盖 EPMS 自己有 model 的表(pr/po/pa/agr/invoice/gr)。
# OA 报销、Finance 预算计划、VMS 访客也往这张共享 tasks 表写 approve_*/revise_*,
# 它们的任务在这里没有任何网,完全依赖各自服务自己关。2026-09-09 生产是 0 僵尸,
# 那是那些队列的现状,不是覆盖。


async def _complete_stale_doc_tasks(db: AsyncSession, Model, document_type: str,
                                    task_type: str, live_statuses: tuple[str, ...]) -> None:
    """Close one task type whose document has left the status it acts on.

    completed_by is deliberately left NULL: this is a system completion, not a
    user dismissal (see _already_surfaced for why that distinction matters).
    """
    stale_q = (
        select(Task)
        .join(Model, Model.id == Task.document_id)
        .where(
            Task.type == task_type,
            Task.document_type == document_type,
            Task.is_completed.is_(False),
            Model.status.notin_(live_statuses),
        )
    )
    now = datetime.now(timezone.utc)
    for task in (await db.execute(stale_q)).scalars().all():
        task.is_completed = True
        task.completed_at = now


async def _complete_stale_status_tasks(db: AsyncSession) -> None:
    """Drive TASK_LIVENESS: close every task that outlived its document's status."""
    for task_type, live in TASK_LIVENESS.items():
        for document_type in live.document_types:
            await _complete_stale_doc_tasks(
                db, live.model, document_type, task_type, live.live_statuses)
    await db.flush()
async def _complete_stale_create_po_tasks(db: AsyncSession) -> None:
    """Auto-complete any open create_po tasks where the linked PR already has a PO."""
    stale_q = (
        select(Task)
        .join(PurchaseRequest, PurchaseRequest.id == Task.document_id)
        .where(
            Task.type == "create_po",
            Task.is_completed.is_(False),
            # PR has a PO via either back-ref (pr.po_id) OR the PO side (po.pr_id,
            # the case when the PO was imported after the PR and pr.po_id was
            # never backfilled) — complete the task in both.
            or_(
                PurchaseRequest.po_id.is_not(None),
                PurchaseRequest.id.in_(
                    select(PurchaseOrder.pr_id).where(PurchaseOrder.pr_id.is_not(None))
                ),
            ),
        )
    )
    result = await db.execute(stale_q)
    now = datetime.now(timezone.utc)
    for task in result.scalars().all():
        task.is_completed = True
        task.completed_at = now
    await db.flush()


async def _backfill_create_po_tasks(db: AsyncSession) -> None:
    """Create create_po tasks for approved PRs that still have no PO.

    The live flow raises this task in the approval engine's _post_approve_pr
    when a PR becomes fully approved. PMS-imported PRs land in 'approved' state
    without going through that engine hook, so they never got a Create PO task —
    unlike place_order, which _backfill_place_order_tasks already covers. This
    is the symmetric safety net so the purchasing office actually sees the work.

    Scope: status='approved' AND po_id IS NULL (a PR with a PO needs no task;
    _complete_stale_create_po_tasks completes any that fell through the cracks).
    """
    approved_prs_q = select(PurchaseRequest).where(
        PurchaseRequest.status == "approved",
        PurchaseRequest.po_id.is_(None),
        # A PR whose PO was imported later carries the link only on the PO side
        # (po.pr_id) — pr.po_id may still be NULL. Exclude those too, else this
        # keeps re-raising a create_po task for a PR that already HAS a PO.
        PurchaseRequest.id.not_in(
            select(PurchaseOrder.pr_id).where(PurchaseOrder.pr_id.is_not(None))
        ),
        PurchaseRequest.created_at >= _BACKFILL_MIN_CREATED,
    )
    approved_prs = (await db.execute(approved_prs_q)).scalars().all()
    if not approved_prs:
        return

    # Don't re-raise for a PR whose create_po task is still open OR was
    # user-dismissed via Mark Done (see _already_surfaced).
    pr_ids_with_task = set(
        (await db.execute(_already_surfaced("create_po", "pr"))).scalars().all()
    )

    for pr in approved_prs:
        if pr.id not in pr_ids_with_task:
            db.add(Task(
                type="create_po",
                priority="normal",
                document_type="pr",
                document_id=pr.id,
                document_number=pr.number,
                assigned_role="procurement_officer",
                title=f"Create PO: {pr.number} — {pr.title}",
                description=f"PR {pr.number} has been fully approved. Please create a Purchase Order.",
                amount=pr.amount,
                vendor=pr.vendor_name,
            ))
    await db.flush()


async def _backfill_place_order_tasks(db: AsyncSession) -> None:
    """Create place_order tasks for approved POs that have no such open task yet."""
    # Find approved POs that haven't been placed
    approved_pos_q = select(PurchaseOrder).where(
        PurchaseOrder.status == "approved",
        PurchaseOrder.place_order_method.is_(None),
        PurchaseOrder.created_at >= _BACKFILL_MIN_CREATED,
    )
    approved_pos = (await db.execute(approved_pos_q)).scalars().all()
    if not approved_pos:
        return

    # Skip POs whose place_order task is still open OR was user-dismissed via
    # Mark Done (see _already_surfaced) — a manual dismissal is permanent.
    po_ids_with_task = set(
        (await db.execute(_already_surfaced("place_order", "po"))).scalars().all()
    )

    for po in approved_pos:
        if po.id not in po_ids_with_task:
            db.add(Task(
                type="place_order",
                priority="normal",
                document_type="po",
                document_id=po.id,
                document_number=po.number,
                assigned_role="procurement_officer",
                title=f"Place Order: {po.number} — {po.title}",
                description=f"PO {po.number} has been approved. Please place the order with the vendor.",
                amount=po.total,
                vendor=po.vendor_name,
            ))
    await db.flush()


async def _backfill_create_pa_tasks(db: AsyncSession) -> None:
    """Create create_pa tasks for payable POs whose matched invoices await a PA.

    The live flow raises this when an invoice is matched to a PO (see
    api/v1/invoices.py). PMS-imported invoices are pre-matched in bulk without
    that hook, so payable POs never prompted the requester to raise the Payment
    Application. Scope tightly — most historical matched invoices sit on
    closed/cancelled POs that must NOT get a task:
      - PO status still payable (issued / partially_received / fully_received),
      - has a matched invoice created >= the backfill floor,
      - no PA exists for the PO yet,
      - no open create_pa task already.
    Assigns to the PR requester (PO->PR->created_by, else PO.created_by), the
    same assignee the live task uses.
    """
    pos_with_pa = po_ids_with_pa()
    # Skip POs whose create_pa task is still open OR was user-dismissed via
    # Mark Done (see _already_surfaced).
    pos_with_open_task = _already_surfaced("create_pa", "po")
    recent_matched_pos = select(Invoice.po_id).where(
        Invoice.status == "matched",
        Invoice.po_id.is_not(None),
        Invoice.gr_id.is_not(None),
        Invoice.created_at >= _BACKFILL_MIN_CREATED,
    )
    # 一票多 PO:表头之外的 PO 只在 invoice_po_allocations 里出现,header-only 的
    # 查询看不见它们(生产 PO-400-2607-12 就这么漏掉的)。发票的 gr_id 可能是
    # 别的 PO 的 GR,所以这里的收货证据取「本 PO 自己有 GR」。
    #
    # ★ 这个自愈扫描器**故意比 crud.po.po_has_receipt_evidence 窄**:后者还认
    # po_line_items.received_qty(PMS 迁移来的历史单收货量在那里、库里没有 GR
    # 行),照搬过来会让这个扫描器一次性给上百张历史 PO 补发 create_pa。补发
    # 由真实事件驱动(match / GR 创建)就够了,扫描器只兜有 GR 单据的那部分。
    recent_matched_alloc_pos = (
        select(InvoicePoAllocation.po_id)
        .join(Invoice, Invoice.id == InvoicePoAllocation.invoice_id)
        .where(
            Invoice.status == "matched",
            Invoice.created_at >= _BACKFILL_MIN_CREATED,
        )
    )
    pos_with_gr = select(GoodsReceipt.po_id)
    candidates_q = select(PurchaseOrder).where(
        or_(
            PurchaseOrder.status.in_(("issued", "partially_received", "fully_received")),
            # A Service/Project PO can be received while still 'approved' — the
            # service GR branch in api/v1/gr.py allows a GR from 'approved'
            # onward, and place_order is never run for a service engagement that
            # was simply performed. Prod PO-192-2608-01 sat exactly there: GR
            # created, invoice matched, no PA, and the status filter kept this
            # self-heal from ever rescuing it. Admitted only WITH a GR, so an
            # approved-but-unreceived PO still cannot be prompted to pay.
            and_(PurchaseOrder.status == "approved",
                 PurchaseOrder.id.in_(pos_with_gr)),
        ),
        or_(
            PurchaseOrder.id.in_(recent_matched_pos),
            and_(PurchaseOrder.id.in_(recent_matched_alloc_pos),
                 PurchaseOrder.id.in_(pos_with_gr)),
        ),
        PurchaseOrder.id.not_in(pos_with_pa),
        PurchaseOrder.id.not_in(pos_with_open_task),
    )
    pos = (await db.execute(candidates_q)).scalars().all()
    if not pos:
        return

    for po in pos:
        # NC-imported POs have no PR/requester → route to the erp_pa_officer pool
        # (broadcast, NULL assignee), matching the live invoice-match hook. Only
        # NC POs; other PR-less (direct) POs keep the requester/PO-creator route.
        if po.source == "nc":
            assigned_role, assigned_user_id = "erp_pa_officer", None
        else:
            requester_id = None
            if po.pr_id:
                requester_id = (await db.execute(
                    select(PurchaseRequest.created_by).where(PurchaseRequest.id == po.pr_id)
                )).scalar_one_or_none()
            if requester_id is None:
                requester_id = po.created_by
            assigned_role, assigned_user_id = "requester", requester_id
        db.add(Task(
            type="create_pa",
            priority="normal",
            document_type="po",
            document_id=po.id,
            document_number=po.number,
            assigned_role=assigned_role,
            assigned_user_id=assigned_user_id,
            title=f"Create Payment Application for {po.number}",
            description=f"Invoices matched to PO {po.number} await a Payment Application.",
            amount=po.total,
            vendor=po.vendor_name,
        ))
    await db.flush()


async def _backfill_prepayment_pa_tasks(db: AsyncSession) -> None:
    """Fix existing create_prepayment_pa tasks where assigned_user_id is NULL.

    Tasks created before the bug-fix (2026-05) were stored with
    assigned_user_id=NULL + assigned_role="requester", causing every Requester
    to see them.  This backfill resolves assigned_user_id to:
      1. The created_by of the PR linked to the PO, or
      2. The created_by of the PO itself (direct POs without a PR).
    Runs on every GET /tasks call; the SELECT is fast because it only touches
    NULL-assigned broadcast tasks.
    """
    broadcast_q = select(Task).where(
        Task.type == "create_prepayment_pa",
        Task.is_completed.is_(False),
        Task.assigned_user_id.is_(None),
    )
    tasks = (await db.execute(broadcast_q)).scalars().all()
    if not tasks:
        return

    for task in tasks:
        po_row = await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == task.document_id)
        )
        po = po_row.scalar_one_or_none()
        if po is None:
            continue
        requester_id = None
        if po.pr_id:
            pr_row = await db.execute(
                select(PurchaseRequest.created_by).where(PurchaseRequest.id == po.pr_id)
            )
            requester_id = pr_row.scalar_one_or_none()
        if requester_id is None:
            requester_id = po.created_by
        task.assigned_user_id = requester_id

    await db.flush()


async def _all_roles_for_user(db: AsyncSession, base_role: str, user_id: uuid.UUID) -> set[str]:
    """
    Return the full set of roles this user holds.

    The JWT carries only the user's single base role (User.role). Special roles
    (procurement_manager, gm, opm, finance_manager, vendor_manager, finance_bp) are
    ADDITIONAL roles held in identity's user_roles table (same physical DB —
    phase 3 retired the old company_config.role_management assignments).
    Approval tasks for those steps are role-broadcast (assigned_user_id=NULL,
    assigned_role="procurement_manager"), so a dept_manager who is also the
    configured procurement_manager would miss those tasks without this expansion.

    Delegates to access_scope's shared union helper.
    """
    from app.core.access_scope import _effective_role_codes
    return await _effective_role_codes(db, base_role, user_id)


# Approval roles that are ALWAYS routed to a SPECIFIC user — the department's
# Director / the requester's Supervisor — or the whole step is auto-skipped
# (approval-api engine._should_skip_step). They are never a company-wide pool
# the way gm/opm/procurement_manager/finance_* are, so a NULL-assignee task
# carrying one of these roles is an anomaly (a stray re-sync / import / legacy
# row), NOT something to broadcast. Broadcasting it put approval tasks for
# departments a Director doesn't manage into every Director's inbox (the
# cross-department task-leak). Excluded from the role-broadcast branch below;
# holders still receive these tasks when assigned to them specifically. Mirrors
# the dept_manager guard in approval-api (_create_approve_task raises rather than
# leave a NULL-assignee dept_manager task).
_PERSONAL_APPROVAL_ROLES = frozenset({"director", "supervisor"})


async def get_for_role(
    db: AsyncSession,
    role: str,
    user_id: uuid.UUID,
    *,
    is_completed: bool | None = None,
) -> list[Task]:
    """
    Return tasks where:
      - assigned_role matches ANY role the user holds (base + special roles from
        Role Management) — EXCEPT the personal-routed roles (director/supervisor),
        which never broadcast; only their specific assignee sees them — OR
      - assigned_user_id matches the user (for personally assigned tasks).
    system_admin sees all tasks.
    is_completed=None returns open tasks (default), True returns completed tasks.
    """
    # Serialize the read-side backfills across concurrent GET /tasks. The inbox
    # fires several requests at once (header badge + open list + completed list),
    # all of which run these backfills. Without this, two could both read "this
    # doc has no task" and each INSERT one, producing duplicate open rows (the
    # Task Inbox duplication bug). A transaction-scoped advisory lock makes the
    # read-check-insert atomic across requests; it releases automatically when
    # the request's transaction commits.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('epms:task_backfill'))"))

    await _complete_stale_status_tasks(db)
    await _complete_stale_create_po_tasks(db)
    await _complete_stale_create_pa_tasks(db)
    await _complete_orphan_create_pa_tasks(db)
    await _complete_stale_place_order_tasks(db)
    await _complete_stale_create_prepayment_pa_tasks(db)
    await _backfill_create_po_tasks(db)
    await _backfill_place_order_tasks(db)
    await _backfill_create_pa_tasks(db)
    await _backfill_prepayment_pa_tasks(db)

    q = select(Task)
    if role != "system_admin":
        all_roles = await _all_roles_for_user(db, role, user_id)
        broadcast_roles = all_roles - _PERSONAL_APPROVAL_ROLES
        from app.core.delegation import active_delegator_ids, delegated_broadcast_roles
        delegator_ids = await active_delegator_ids(db, user_id)
        deleg_roles = await delegated_broadcast_roles(db, delegator_ids)
        clauses = [
            and_(Task.assigned_user_id.is_(None), Task.assigned_role.in_(broadcast_roles)),
            Task.assigned_user_id == user_id,
        ]
        if delegator_ids:
            # Delegation covers APPROVAL tasks only — never create_po / create_pa
            # / GR acknowledgement, which are role pools that do not strand and
            # whose actions are gated by the Access Control matrix.
            clauses.append(and_(
                Task.type.like("approve%"),
                Task.assigned_user_id.in_(delegator_ids)))
            if deleg_roles:
                clauses.append(and_(
                    Task.type.like("approve%"),
                    Task.assigned_user_id.is_(None),
                    Task.assigned_role.in_(deleg_roles)))
        q = q.where(or_(*clauses))
    completed = is_completed if is_completed is not None else False
    q = q.where(Task.is_completed.is_(completed))
    result = await db.execute(q.order_by(Task.created_at.desc()))
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, task_id: uuid.UUID) -> Task | None:
    result = await db.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one_or_none()
