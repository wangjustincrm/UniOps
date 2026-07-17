"""CRUD for Task inbox."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gr import GoodsReceipt
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task


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


async def _complete_stale_create_po_tasks(db: AsyncSession) -> None:
    """Auto-complete any open create_po tasks where the linked PR already has a PO."""
    stale_q = (
        select(Task)
        .join(PurchaseRequest, PurchaseRequest.id == Task.document_id)
        .where(
            Task.type == "create_po",
            Task.is_completed.is_(False),
            PurchaseRequest.po_id.is_not(None),
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
    _complete_stale_create_po_tasks completes any that slipped through).
    """
    approved_prs_q = select(PurchaseRequest).where(
        PurchaseRequest.status == "approved",
        PurchaseRequest.po_id.is_(None),
    )
    approved_prs = (await db.execute(approved_prs_q)).scalars().all()
    if not approved_prs:
        return

    pr_ids_with_task_q = select(Task.document_id).where(
        Task.type == "create_po",
        Task.is_completed.is_(False),
        Task.document_type == "pr",
    )
    pr_ids_with_task = set((await db.execute(pr_ids_with_task_q)).scalars().all())

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
    )
    approved_pos = (await db.execute(approved_pos_q)).scalars().all()
    if not approved_pos:
        return

    # Find which of those already have an open place_order task
    po_ids_with_task_q = select(Task.document_id).where(
        Task.type == "place_order",
        Task.is_completed.is_(False),
        Task.document_type == "po",
    )
    po_ids_with_task = set((await db.execute(po_ids_with_task_q)).scalars().all())

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
        Role Management), OR
      - assigned_user_id matches the user (for personally assigned tasks).
    system_admin sees all tasks.
    is_completed=None returns open tasks (default), True returns completed tasks.
    """
    await _complete_stale_create_po_tasks(db)
    await _complete_stale_create_pa_tasks(db)
    await _complete_stale_create_prepayment_pa_tasks(db)
    await _backfill_create_po_tasks(db)
    await _backfill_place_order_tasks(db)
    await _backfill_prepayment_pa_tasks(db)

    q = select(Task)
    if role != "system_admin":
        all_roles = await _all_roles_for_user(db, role, user_id)
        q = q.where(
            or_(
                and_(Task.assigned_user_id.is_(None), Task.assigned_role.in_(all_roles)),
                Task.assigned_user_id == user_id,
            )
        )
    completed = is_completed if is_completed is not None else False
    q = q.where(Task.is_completed.is_(completed))
    result = await db.execute(q.order_by(Task.created_at.desc()))
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, task_id: uuid.UUID) -> Task | None:
    result = await db.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one_or_none()
