"""CRUD for Task inbox."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import PurchaseAgreement
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
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
    pos_with_pa = select(PaymentApplication.po_id).where(PaymentApplication.po_id.is_not(None))
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
    pos_with_pa = select(PaymentApplication.po_id).where(
        PaymentApplication.po_id.is_not(None)
    )
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
    pos_with_prepayment = select(PaymentApplication.po_id).where(
        PaymentApplication.po_id.is_not(None),
        PaymentApplication.pa_type == "prepayment",
    )
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


async def _complete_stale_approve_tasks(db: AsyncSession) -> None:
    """Auto-complete open approve_* tasks whose document already left the
    approvable state (status not in submitted/in_review).

    When a PR/PO/PA is approved, returned, cancelled or (for a PO) issued, the
    approval engine completes its approve task in the same transaction. Drift
    still happens — config re-syncs, imports, or status flips that bypass the
    engine (e.g. a PO reaching 'issued', a PA paid via finance-api) — leaving an
    OPEN approve task on a terminal document. That is a phantom "pending
    approval" that 409s on click: it shows in the Task Inbox (which gates only on
    is_completed) but NOT on the Dashboard (which additionally gates on doc
    status), so the two surfaces disagree. This mirrors the on-demand cleanup in
    approval-api engine._resync_document so they reconcile without a manual
    re-sync. Only approve_* tasks are touched — never legitimate next-step work
    (place_order / create_pa) on an already-done document.
    """
    approvable = ("submitted", "in_review")
    doc_specs = (
        ("pr", "approve_pr", PurchaseRequest),
        ("po", "approve_po", PurchaseOrder),
        ("pa", "approve_pa", PaymentApplication),
        # Purchase Agreements leave the approvable set the same ways a PO does
        # (approved -> "active", returned, cancelled) plus one route nothing
        # else has: expiry. Omitting them here reproduces exactly the Task
        # Inbox / Dashboard split this function was written to end.
        ("agr", "approve_agr", PurchaseAgreement),
    )
    now = datetime.now(timezone.utc)
    for doc_type, task_type, Model in doc_specs:
        stale_q = (
            select(Task)
            .join(Model, Model.id == Task.document_id)
            .where(
                Task.type == task_type,
                Task.document_type == doc_type,
                Task.is_completed.is_(False),
                Model.status.notin_(approvable),
            )
        )
        for task in (await db.execute(stale_q)).scalars().all():
            task.is_completed = True
            task.completed_at = now
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
    pos_with_pa = select(PaymentApplication.po_id).where(PaymentApplication.po_id.is_not(None))
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
    # 别的 PO 的 GR,所以这里的收货证据取「本 PO 自己有 GR」——与
    # crud.po.po_has_three_way_matched_invoice 同口径。
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
        PurchaseOrder.status.in_(("issued", "partially_received", "fully_received")),
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

    await _complete_stale_approve_tasks(db)
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
