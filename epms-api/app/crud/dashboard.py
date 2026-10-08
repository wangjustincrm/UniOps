"""CRUD / aggregation queries for the Dashboard API."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.delegation import active_delegator_ids, delegated_broadcast_roles
from app.models.config import CompanyConfig
from app.models.department import Department
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.crud.pa_links import pa_ids_for_po, pa_ids_for_pos
from app.models.pa import PaymentApplication
from app.models.pa_po_link import PaPoLink
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.dashboard import (
    ApprovalItem,
    BudgetGroupRow,
    BudgetOverview,
    DashboardResponse,
    GrRow,
    InvoiceRow,
    KpiCard,
    PaOverview,
    PaRow,
    PipelineGr,
    PipelineInvoice,
    PipelinePa,
    PipelinePo,
    PoRow,
    PrPipelineItem,
    RoleCount,
    StatusBreakdown,
    VendorRow,
)

_ZERO = Decimal("0")


def _today() -> date:
    return datetime.now(tz=timezone.utc).date()


async def _get_budget_thresholds(db: AsyncSession) -> tuple[float, float]:
    """Return (yellow_threshold_pct, red_threshold_pct) from company config."""
    result = await db.execute(select(CompanyConfig).limit(1))
    cfg = result.scalar_one_or_none()
    if cfg and cfg.budget_admin_config:
        yellow = float(cfg.budget_admin_config.get("yellow_threshold_pct", 80.0))
        red = float(cfg.budget_admin_config.get("red_threshold_pct", 100.0))
        return yellow, red
    return 80.0, 100.0


def _fmt(amount: Decimal, currency: str = "CAD") -> str:
    return f"{currency} {amount:,.2f}"


def _days_ago(dt: datetime | None) -> int:
    if dt is None:
        return 0
    now = datetime.now(tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (now - dt).days)


# ── Budget helpers ───────────────────────────────────────────────────────────
# Budget data now lives in budget-api (:8007). The dashboard widgets that
# render budget overview should call budget-api directly from the frontend
# (GET /api/v1/actuals/summary). These helpers return empty/zero data
# (call-site stability only).

async def _budget_overview(
    db: AsyncSession,  # noqa: ARG001
    yellow_threshold: float = 80.0,  # noqa: ARG001
    red_threshold: float = 100.0,  # noqa: ARG001
) -> BudgetOverview:
    return BudgetOverview(
        total_budget=_ZERO, total_committed=_ZERO,
        total_spent=_ZERO, utilisation_pct=0.0,
        groups=[],
    )


async def _over_budget_count(
    db: AsyncSession,  # noqa: ARG001
    red_threshold: float = 100.0,  # noqa: ARG001
) -> int:
    return 0


# ── Pending approvals (PRs + POs + PAs in submitted/in_review) ───────────────

async def _effective_roles(db: AsyncSession, role: str, user_id: uuid.UUID) -> set[str]:
    """Expand base JWT role with any ADDITIONAL roles held via identity's
    user_roles table (same physical DB). Delegates to access_scope's shared
    union helper — replaces the retired company_config.role_management
    assignments (phase 3)."""
    from app.core.access_scope import _effective_role_codes
    return await _effective_role_codes(db, role, user_id)


def _task_subq(
    doc_type: str, task_type: str, user_id: uuid.UUID, effective_roles: set[str],
    delegator_ids: set[uuid.UUID] = frozenset(),
    delegated_roles: set[str] = frozenset(),
):
    """Subquery of document_ids where the user has an open approval task.

    `delegator_ids` widens "own" to also match approve tasks assigned to
    anyone this user is standing in for today (resolved once per request by
    the caller, not here) — restricted to approve% so a delegate never
    inherits a delegator's non-approval workload.

    `delegated_roles` (from app.core.delegation.delegated_broadcast_roles)
    mirrors that same widening for ROLE-POOL approve tasks (assigned_user_id
    IS NULL, assigned_role = a role a delegator holds) — the counterpart to
    `app/crud/task.py`'s `get_for_role`, which already folds this in for the
    Task Inbox. Before this, a delegate covering a role-pool poster (e.g.
    Finance Manager) saw the approve_pa task in their inbox but got no
    Pending-Approvals row for it here, contradicting this function's own
    docstring that the two surfaces always agree. `task_type` is always an
    "approve_*" type at every call site, so it already gates this branch to
    approval tasks — no separate approve% filter is needed here.
    """
    own = Task.assigned_user_id == user_id
    if delegator_ids:
        own = or_(own, and_(
            Task.type.like("approve%"), Task.assigned_user_id.in_(delegator_ids),
        ))
    return select(Task.document_id).where(
        Task.document_type == doc_type,
        Task.type == task_type,
        Task.is_completed.is_(False),
        or_(
            own,
            and_(Task.assigned_user_id.is_(None),
                 Task.assigned_role.in_(effective_roles | delegated_roles)),
        ),
    )


async def _pending_approvals(
    db: AsyncSession,
    user_id: uuid.UUID,
    role: str,
    limit: int = 8,
) -> list[ApprovalItem]:
    """Return documents with open approval tasks assigned to this user.

    Uses the same task-based filtering as the Task Inbox so the two surfaces
    always agree — a document only appears here if the user has an open
    approve_* task for it (via personal assignment or role broadcast).
    """
    eff_roles = await _effective_roles(db, role, user_id)
    # Resolved once per request, then reused across the PR/PO/PA subqueries below.
    delegator_ids = await active_delegator_ids(db, user_id)
    delegated_roles = await delegated_broadcast_roles(db, delegator_ids)
    items: list[ApprovalItem] = []

    # PRs — join creator + department for context
    pr_subq = _task_subq("pr", "approve_pr", user_id, eff_roles, delegator_ids, delegated_roles)
    pr_rows = await db.execute(
        select(
            PurchaseRequest,
            User.full_name.label("requester_name"),
            Department.name.label("dept_name"),
        )
        .join(User, User.id == PurchaseRequest.created_by, isouter=True)
        .join(Department, Department.id == User.department_id, isouter=True)
        .where(
            PurchaseRequest.status.in_(["submitted", "in_review"]),
            PurchaseRequest.id.in_(pr_subq),
        )
        .order_by(PurchaseRequest.submitted_at)
        .limit(limit)
    )
    for pr, requester_name, dept_name in pr_rows:
        items.append(ApprovalItem(
            id=pr.id, doc_type="PR", number=pr.number, title=pr.title,
            amount=pr.amount or _ZERO, currency=pr.currency, status=pr.status,
            submitted_days_ago=_days_ago(pr.submitted_at),
            href=f"/pr/{pr.id}",
            requester_name=requester_name or "",
            dept_name=dept_name or "",
        ))

    # POs — filter by open approve_po tasks for this user
    po_subq = _task_subq("po", "approve_po", user_id, eff_roles, delegator_ids, delegated_roles)
    po_rows = await db.execute(
        select(
            PurchaseOrder,
            User.full_name.label("requester_name"),
            Department.name.label("dept_name"),
        )
        .join(User, User.id == PurchaseOrder.created_by, isouter=True)
        .join(Department, Department.id == User.department_id, isouter=True)
        .where(
            PurchaseOrder.status.in_(["submitted", "in_review"]),
            PurchaseOrder.id.in_(po_subq),
        )
        .order_by(PurchaseOrder.created_at)
        .limit(limit)
    )
    for po, requester_name, dept_name in po_rows:
        items.append(ApprovalItem(
            id=po.id, doc_type="PO", number=po.number, title=po.title,
            amount=po.total or _ZERO, currency=po.currency, status=po.status,
            submitted_days_ago=_days_ago(po.created_at),
            href=f"/po/{po.id}",
            requester_name=requester_name or "",
            dept_name=dept_name or "",
        ))

    # PAs — filter by open approve_pa tasks for this user
    pa_subq = _task_subq("pa", "approve_pa", user_id, eff_roles, delegator_ids, delegated_roles)
    pa_rows = await db.execute(
        select(
            PaymentApplication,
            User.full_name.label("requester_name"),
            Department.name.label("dept_name"),
        )
        .join(User, User.id == PaymentApplication.created_by, isouter=True)
        .join(Department, Department.id == User.department_id, isouter=True)
        .where(
            PaymentApplication.status.in_(["submitted", "in_review"]),
            PaymentApplication.id.in_(pa_subq),
        )
        .order_by(PaymentApplication.submitted_at)
        .limit(limit)
    )
    for pa, requester_name, dept_name in pa_rows:
        items.append(ApprovalItem(
            id=pa.id, doc_type="PA", number=pa.pa_number, title=pa.title,
            amount=pa.payment_amount or _ZERO, currency=pa.currency, status=pa.status,
            submitted_days_ago=_days_ago(pa.submitted_at),
            href=f"/pa/{pa.id}",
            requester_name=requester_name or "",
            dept_name=dept_name or "",
        ))

    items.sort(key=lambda x: x.submitted_days_ago, reverse=True)
    return items[:limit]


# ── PR Pipeline ──────────────────────────────────────────────────────────────

async def _pr_pipeline(db: AsyncSession, user_id: uuid.UUID) -> list[PrPipelineItem]:
    """Active PRs for a requester with full document chain."""
    pr_result = await db.execute(
        select(PurchaseRequest)
        .where(
            PurchaseRequest.created_by == user_id,
            PurchaseRequest.status.notin_(["cancelled", "rejected"]),
        )
        .order_by(PurchaseRequest.created_at.desc())
        .limit(10)
    )
    prs = list(pr_result.scalars().all())

    pipeline: list[PrPipelineItem] = []
    for pr in prs:
        po_result = await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.pr_id == pr.id)
        )
        pos_orm = list(po_result.scalars().all())
        po_items: list[PipelinePo] = []

        for po in pos_orm:
            # GRs
            gr_result = await db.execute(
                select(GoodsReceipt).where(GoodsReceipt.po_id == po.id)
            )
            grs = [PipelineGr(id=g.id, number=g.number, gr_type=g.gr_type, status=g.status)
                   for g in gr_result.scalars()]

            # Invoices
            inv_result = await db.execute(
                select(Invoice).where(Invoice.po_id == po.id)
            )
            invoices = [PipelineInvoice(
                id=i.id, internal_ref=i.internal_ref,
                status=i.status, total_amount=i.total_amount,
            ) for i in inv_result.scalars()]

            # PAs
            pa_result = await db.execute(
                select(PaymentApplication)
                .where(PaymentApplication.id.in_(pa_ids_for_po(po.id)))
            )
            pas = [PipelinePa(
                id=p.id, pa_number=p.pa_number,
                status=p.status, payment_amount=p.payment_amount,
            ) for p in pa_result.scalars()]

            po_items.append(PipelinePo(
                id=po.id, number=po.number, status=po.status,
                total=po.total or _ZERO, vendor_name=po.vendor_name or "",
                grs=grs, invoices=invoices, pas=pas,
            ))

        pipeline.append(PrPipelineItem(
            id=pr.id, number=pr.number, title=pr.title,
            status=pr.status, amount=pr.amount or _ZERO, currency=pr.currency,
            pos=po_items,
        ))
    return pipeline


# ── Status breakdown (auditor) ───────────────────────────────────────────────

async def _status_breakdown(db: AsyncSession) -> StatusBreakdown:
    async def counts(model, status_col):
        result = await db.execute(
            select(status_col, func.count()).group_by(status_col)
        )
        return {row[0]: row[1] for row in result}

    return StatusBreakdown(
        pr=await counts(PurchaseRequest, PurchaseRequest.status),
        po=await counts(PurchaseOrder, PurchaseOrder.status),
        gr=await counts(GoodsReceipt, GoodsReceipt.status),
        pa=await counts(PaymentApplication, PaymentApplication.status),
    )


# ── PA helpers ───────────────────────────────────────────────────────────────

async def _pa_overview(db: AsyncSession) -> PaOverview:
    result = await db.execute(select(PaymentApplication))
    all_pas = list(result.scalars().all())
    pending = [p for p in all_pas if p.status in ("submitted", "in_review", "approved")]
    processed = [p for p in all_pas if p.status == "processed"]
    return PaOverview(
        total_count=len(all_pas),
        pending_count=len(pending),
        pending_value=sum((p.payment_amount for p in pending), _ZERO),
        processed_value=sum((p.payment_amount for p in processed), _ZERO),
    )


async def _pa_rows(db: AsyncSession, statuses: list[str], limit: int = 8) -> list[PaRow]:
    result = await db.execute(
        select(PaymentApplication)
        .where(PaymentApplication.status.in_(statuses))
        .order_by(PaymentApplication.created_at.desc())
        .limit(limit)
    )
    pas = list(result.scalars())
    numbers_by_pa: dict[uuid.UUID, list[str]] = {}
    if pas:
        for pa_id, po_number in (await db.execute(
            select(PaPoLink.pa_id, PaPoLink.po_number)
            .where(PaPoLink.pa_id.in_([p.id for p in pas]))
            .order_by(PaPoLink.sort_order)
        )).all():
            numbers_by_pa.setdefault(pa_id, []).append(po_number)
    return [PaRow(
        id=p.id, pa_number=p.pa_number, vendor_name=p.vendor_name,
        po_number=p.po_number, po_numbers=numbers_by_pa.get(p.id, []),
        pa_type=p.pa_type, payment_amount=p.payment_amount,
        currency=p.currency, status=p.status, created_at=p.created_at,
    ) for p in pas]


# ── Role-specific dashboard builders ────────────────────────────────────────

async def build_requester(db: AsyncSession, user_id: uuid.UUID) -> DashboardResponse:
    # Active = in-progress only. Exclude terminal states (cancelled/rejected) AND
    # completed ones (issued → a PO was raised): the card reads "In progress".
    pr_result = await db.execute(
        select(func.count()).select_from(PurchaseRequest).where(
            PurchaseRequest.created_by == user_id,
            PurchaseRequest.status.notin_(["cancelled", "rejected", "issued"]),
        )
    )
    active_prs = pr_result.scalar_one()

    # Tasks overdue — widened to the delegator's overdue APPROVE tasks while a
    # delegation is active, restricted to approve% so the delegate's overdue
    # count is never inflated by the delegator's other (non-approval) work.
    delegator_ids = await active_delegator_ids(db, user_id)
    own_task = Task.assigned_user_id == user_id
    if delegator_ids:
        own_task = or_(own_task, and_(
            Task.type.like("approve%"), Task.assigned_user_id.in_(delegator_ids),
        ))
    task_result = await db.execute(
        select(func.count()).select_from(Task).where(
            own_task,
            Task.is_completed.is_(False),
            Task.due_date < _today(),
        )
    )
    overdue_tasks = task_result.scalar_one()

    # Payment KPIs follow the requester's OWN document chain (my PR → PO → PA),
    # not PAs the requester personally created (requesters don't create PAs).
    my_pr_ids = select(PurchaseRequest.id).where(PurchaseRequest.created_by == user_id)
    my_po_ids = select(PurchaseOrder.id).where(PurchaseOrder.pr_id.in_(my_pr_ids))

    # Paid this month — processed PAs in my chain, keyed on paid_at (the real
    # payment date; never bumped by unrelated writes like updated_at was).
    # A PA settling several POs counts in full for every requester who owns one
    # of them. There is no per-PO split of payment_amount to divide by, and the
    # alternative — counting only the primary PO — would make the payment vanish
    # from the dashboard of everyone else it actually paid. This is a personal
    # "what happened on my orders" tile, not a company total, so the whole
    # payment showing up on each owner's tile is the honest reading.
    paid_result = await db.execute(
        select(func.coalesce(func.sum(PaymentApplication.payment_amount), 0)).where(
            PaymentApplication.id.in_(pa_ids_for_pos(my_po_ids)),
            PaymentApplication.status == "processed",
            func.date_trunc("month", PaymentApplication.paid_at) ==
            func.date_trunc("month", func.now()),
        )
    )
    paid_month = Decimal(str(paid_result.scalar_one()))

    # Pending payments — PAs in my chain not yet processed.
    pending_result = await db.execute(
        select(func.coalesce(func.sum(PaymentApplication.payment_amount), 0)).where(
            PaymentApplication.id.in_(pa_ids_for_pos(my_po_ids)),
            PaymentApplication.status.in_(["draft", "submitted", "in_review", "approved"]),
        )
    )
    pending_payments = Decimal(str(pending_result.scalar_one()))

    pipeline = await _pr_pipeline(db, user_id)

    return DashboardResponse(
        role="requester",
        kpis=[
            KpiCard(title="Active PRs", value=str(active_prs)),
            KpiCard(title="Pending Payments", value=_fmt(pending_payments)),
            KpiCard(title="Paid This Month", value=_fmt(paid_month)),
            KpiCard(title="Overdue Tasks", value=str(overdue_tasks), alert=overdue_tasks > 0),
        ],
        pr_pipeline=pipeline,
    )


async def build_approver(db: AsyncSession, user_id: uuid.UUID, role: str) -> DashboardResponse:
    # Pending approvals count — filtered to this user's assigned tasks only
    pending_items = await _pending_approvals(db, user_id, role)
    pending_count = len(pending_items)

    # Open POs
    po_open = await db.execute(
        select(func.count()).select_from(PurchaseOrder).where(
            PurchaseOrder.status.notin_(["cancelled", "closed", "draft"])
        )
    )
    open_pos = po_open.scalar_one()

    # Total committed (active POs)
    committed_result = await db.execute(
        select(func.coalesce(func.sum(PurchaseOrder.total), 0)).where(
            PurchaseOrder.status.notin_(["cancelled", "closed", "draft"])
        )
    )
    total_committed = Decimal(str(committed_result.scalar_one()))

    yellow_thresh, red_thresh = await _get_budget_thresholds(db)
    over_budget = await _over_budget_count(db, red_threshold=red_thresh)
    budget = await _budget_overview(db, yellow_threshold=yellow_thresh, red_threshold=red_thresh)

    return DashboardResponse(
        role="approver",
        kpis=[
            KpiCard(title="Pending Approvals", value=str(pending_count), alert=pending_count > 0),
            KpiCard(title="Open POs", value=str(open_pos)),
            KpiCard(title="Total Committed (FY)", value=_fmt(total_committed)),
            KpiCard(title="Over-Budget Depts", value=str(over_budget), alert=over_budget > 0),
        ],
        pending_approvals=pending_items,
        budget_overview=budget,
    )


async def build_procurement(db: AsyncSession) -> DashboardResponse:
    open_po = await db.execute(
        select(func.count()).select_from(PurchaseOrder).where(
            PurchaseOrder.status.notin_(["cancelled", "closed", "draft"])
        )
    )
    pending_po = await db.execute(
        select(func.count()).select_from(PurchaseOrder).where(
            PurchaseOrder.status.in_(["submitted", "in_review"])
        )
    )
    committed = await db.execute(
        select(func.coalesce(func.sum(PurchaseOrder.total), 0)).where(
            PurchaseOrder.status.notin_(["cancelled", "closed", "draft"])
        )
    )
    open_gr = await db.execute(
        select(func.count()).select_from(GoodsReceipt).where(
            GoodsReceipt.status.in_(["pending_ack", "collection_pending"])
        )
    )

    open_pos_count = open_po.scalar_one()
    pending_po_count = pending_po.scalar_one()
    total_committed = Decimal(str(committed.scalar_one()))
    open_grs = open_gr.scalar_one()

    # Recent POs
    recent_pos_result = await db.execute(
        select(PurchaseOrder)
        .order_by(PurchaseOrder.created_at.desc())
        .limit(8)
    )
    recent_pos = [PoRow(
        id=po.id, number=po.number, title=po.title, vendor_name=po.vendor_name,
        total=po.total or _ZERO, currency=po.currency, status=po.status,
        created_at=po.created_at,
    ) for po in recent_pos_result.scalars()]

    return DashboardResponse(
        role="procurement",
        kpis=[
            KpiCard(title="Open POs", value=str(open_pos_count)),
            KpiCard(title="POs Pending Approval", value=str(pending_po_count), alert=pending_po_count > 0),
            KpiCard(title="Total Committed (FY)", value=_fmt(total_committed)),
            KpiCard(title="Open GRs", value=str(open_grs)),
        ],
        recent_pos=recent_pos,
    )


async def build_warehouse(db: AsyncSession) -> DashboardResponse:
    ack_r = await db.execute(
        select(func.count()).select_from(GoodsReceipt).where(GoodsReceipt.status == "pending_ack")
    )
    col_r = await db.execute(
        select(func.count()).select_from(GoodsReceipt).where(GoodsReceipt.status == "collection_pending")
    )
    disc_r = await db.execute(
        select(func.count()).select_from(GoodsReceipt).where(GoodsReceipt.status == "discrepancy")
    )
    done_r = await db.execute(
        select(func.count()).select_from(GoodsReceipt).where(
            GoodsReceipt.status.in_(["confirmed", "collected"]),
            # collected_at is stamped once at collect/confirm — unlike updated_at
            # (onupdate=now), it is never bumped by later unrelated writes.
            func.date_trunc("month", GoodsReceipt.collected_at) == func.date_trunc("month", func.now()),
        )
    )

    # Recent open GRs
    gr_result = await db.execute(
        select(GoodsReceipt)
        .where(GoodsReceipt.status.notin_(["confirmed", "cancelled"]))
        .order_by(GoodsReceipt.created_at.desc())
        .limit(8)
    )
    recent_grs = [GrRow(
        id=g.id, number=g.number, gr_type=g.gr_type, po_number=g.po_number,
        vendor_name=g.vendor_name, currency=g.currency, status=g.status,
        created_at=g.created_at,
    ) for g in gr_result.scalars()]

    ack_count = ack_r.scalar_one()
    disc_count = disc_r.scalar_one()
    return DashboardResponse(
        role="warehouse",
        kpis=[
            KpiCard(title="GRs Awaiting ACK", value=str(ack_count), alert=ack_count > 0),
            KpiCard(title="Collection Pending", value=str(col_r.scalar_one())),
            KpiCard(title="Discrepancies", value=str(disc_count), alert=disc_count > 0),
            KpiCard(title="Completed This Month", value=str(done_r.scalar_one())),
        ],
        recent_grs=recent_grs,
    )


async def build_ap_clerk(db: AsyncSession) -> DashboardResponse:
    unmatched_r = await db.execute(
        select(func.count()).select_from(Invoice).where(Invoice.status == "unmatched")
    )
    exception_r = await db.execute(
        select(func.count()).select_from(Invoice).where(Invoice.status == "exception")
    )
    pa_pending_r = await db.execute(
        select(func.count()).select_from(PaymentApplication).where(
            PaymentApplication.status.in_(["submitted", "in_review"])
        )
    )
    pa_processed_r = await db.execute(
        select(func.count()).select_from(PaymentApplication).where(
            PaymentApplication.status == "processed",
            func.date_trunc("month", PaymentApplication.paid_at) == func.date_trunc("month", func.now()),
        )
    )

    inv_result = await db.execute(
        select(Invoice).order_by(Invoice.created_at.desc()).limit(8)
    )
    recent_invoices = [InvoiceRow(
        id=i.id, internal_ref=i.internal_ref, vendor_name=i.vendor_name,
        po_number=i.po_number, total_amount=i.total_amount, currency=i.currency,
        status=i.status, created_at=i.created_at,
    ) for i in inv_result.scalars()]

    unmatched = unmatched_r.scalar_one()
    exception_count = exception_r.scalar_one()
    return DashboardResponse(
        role="ap_clerk",
        kpis=[
            KpiCard(title="Invoices to Match", value=str(unmatched), alert=unmatched > 0),
            KpiCard(title="Exceptions", value=str(exception_count), alert=exception_count > 0),
            KpiCard(title="PAs Pending", value=str(pa_pending_r.scalar_one())),
            KpiCard(title="Processed This Month", value=str(pa_processed_r.scalar_one())),
        ],
        recent_invoices=recent_invoices,
    )


async def build_payment_officer(db: AsyncSession) -> DashboardResponse:
    """Payment execution split out of AP Clerk (2026-08-13): scoped to PAs that
    have cleared approval and are waiting to be paid, plus this role's own
    throughput. Deliberately excludes ap_clerk's unmatched/exception invoice
    counts — those belong to the AP review job this role is separated from."""
    awaiting_r = await db.execute(
        select(func.count()).select_from(PaymentApplication).where(
            PaymentApplication.status == "approved"
        )
    )
    awaiting_value_r = await db.execute(
        select(func.coalesce(func.sum(PaymentApplication.payment_amount), 0)).where(
            PaymentApplication.status == "approved"
        )
    )
    processed_month_r = await db.execute(
        select(func.count()).select_from(PaymentApplication).where(
            PaymentApplication.status == "processed",
            func.date_trunc("month", PaymentApplication.paid_at) == func.date_trunc("month", func.now()),
        )
    )
    processed_month_value_r = await db.execute(
        select(func.coalesce(func.sum(PaymentApplication.payment_amount), 0)).where(
            PaymentApplication.status == "processed",
            func.date_trunc("month", PaymentApplication.paid_at) == func.date_trunc("month", func.now()),
        )
    )

    awaiting_pas = await _pa_rows(db, ["approved"])

    awaiting_count = awaiting_r.scalar_one()
    return DashboardResponse(
        role="payment_officer",
        kpis=[
            KpiCard(title="PAs Awaiting Payment", value=str(awaiting_count), alert=awaiting_count > 0),
            KpiCard(title="Value Awaiting Payment", value=_fmt(Decimal(str(awaiting_value_r.scalar_one())))),
            KpiCard(title="Processed This Month", value=str(processed_month_r.scalar_one())),
            KpiCard(title="Value Processed This Month", value=_fmt(Decimal(str(processed_month_value_r.scalar_one())))),
        ],
        pa_in_review=awaiting_pas,
    )


async def build_finance_bp(db: AsyncSession) -> DashboardResponse:
    in_review_r = await db.execute(
        select(func.count()).select_from(PaymentApplication).where(
            PaymentApplication.status == "in_review"
        )
    )
    approved_today_r = await db.execute(
        select(func.count()).select_from(PaymentApplication).where(
            PaymentApplication.status == "approved",
            # approved_at is stamped once at the approval transition (approval
            # engine); updated_at would drift on any later edit/re-sync.
            func.date_trunc("day", PaymentApplication.approved_at) == func.date_trunc("day", func.now()),
        )
    )
    pending_value_r = await db.execute(
        select(func.coalesce(func.sum(PaymentApplication.payment_amount), 0)).where(
            PaymentApplication.status == "in_review"
        )
    )
    processed_month_r = await db.execute(
        select(func.count()).select_from(PaymentApplication).where(
            PaymentApplication.status == "processed",
            func.date_trunc("month", PaymentApplication.paid_at) == func.date_trunc("month", func.now()),
        )
    )

    pa_in_review = await _pa_rows(db, ["in_review"])

    in_review_count = in_review_r.scalar_one()
    return DashboardResponse(
        role="finance_bp",
        kpis=[
            KpiCard(title="PAs In Review", value=str(in_review_count), alert=in_review_count > 0),
            KpiCard(title="Approved Today", value=str(approved_today_r.scalar_one())),
            KpiCard(title="Total PA Value Pending", value=_fmt(Decimal(str(pending_value_r.scalar_one())))),
            KpiCard(title="Processed This Month", value=str(processed_month_r.scalar_one())),
        ],
        pa_in_review=pa_in_review,
    )


async def build_finance_manager(db: AsyncSession, user_id: uuid.UUID, role: str) -> DashboardResponse:
    pa_pending_r = await db.execute(
        select(func.count()).select_from(PaymentApplication).where(
            PaymentApplication.status.in_(["submitted", "in_review"])
        )
    )
    yellow_thresh, red_thresh = await _get_budget_thresholds(db)
    over_budget = await _over_budget_count(db, red_threshold=red_thresh)

    open_po_value_r = await db.execute(
        select(func.coalesce(func.sum(PurchaseOrder.total), 0)).where(
            PurchaseOrder.status.in_(["approved", "issued"])
        )
    )
    pending_inv_r = await db.execute(
        select(func.count()).select_from(Invoice).where(
            Invoice.status.notin_(["matched", "paid", "approved"])
        )
    )

    pa_pending_count = pa_pending_r.scalar_one()
    pending_inv = pending_inv_r.scalar_one()

    pending_pas = await _pa_rows(db, ["submitted", "in_review"])
    budget = await _budget_overview(db, yellow_threshold=yellow_thresh, red_threshold=red_thresh)

    return DashboardResponse(
        role="finance_manager",
        kpis=[
            KpiCard(title="PAs Pending Approval", value=str(pa_pending_count), alert=pa_pending_count > 0),
            KpiCard(title="Over-Budget Accounts", value=str(over_budget), alert=over_budget > 0),
            KpiCard(title="Open POs Value", value=_fmt(Decimal(str(open_po_value_r.scalar_one())))),
            KpiCard(title="Pending Invoices", value=str(pending_inv), alert=pending_inv > 0),
        ],
        pending_approvals=await _pending_approvals(db, user_id, role),
        pa_in_review=pending_pas,
        budget_overview=budget,
    )


async def build_cfo(db: AsyncSession) -> DashboardResponse:
    yellow_thresh, red_thresh = await _get_budget_thresholds(db)
    budget = await _budget_overview(db, yellow_threshold=yellow_thresh, red_threshold=red_thresh)
    pa_overview = await _pa_overview(db)

    util_alert = budget.utilisation_pct >= 100.0

    return DashboardResponse(
        role="cfo",
        kpis=[
            KpiCard(title="Total Budget (FY)", value=_fmt(budget.total_budget)),
            KpiCard(title="Total Committed", value=_fmt(budget.total_committed)),
            KpiCard(title="Total Spent", value=_fmt(budget.total_spent)),
            KpiCard(
                title="Budget Utilisation",
                value=f"{budget.utilisation_pct:.1f}%",
                alert=util_alert,
            ),
        ],
        budget_overview=budget,
        pa_overview=pa_overview,
    )


async def build_auditor(db: AsyncSession) -> DashboardResponse:
    pr_r = await db.execute(select(func.count()).select_from(PurchaseRequest))
    po_r = await db.execute(select(func.count()).select_from(PurchaseOrder))
    inv_r = await db.execute(select(func.count()).select_from(Invoice))
    pa_r = await db.execute(select(func.count()).select_from(PaymentApplication))
    breakdown = await _status_breakdown(db)

    return DashboardResponse(
        role="auditor",
        kpis=[
            KpiCard(title="Total PRs", value=str(pr_r.scalar_one())),
            KpiCard(title="Total POs", value=str(po_r.scalar_one())),
            KpiCard(title="Total Invoices", value=str(inv_r.scalar_one())),
            KpiCard(title="Total PAs", value=str(pa_r.scalar_one())),
        ],
        status_breakdown=breakdown,
    )


async def build_vendor_manager(db: AsyncSession) -> DashboardResponse:
    total_r = await db.execute(select(func.count()).select_from(Vendor))
    active_r = await db.execute(
        select(func.count()).select_from(Vendor).where(Vendor.is_active.is_(True))
    )
    inactive_r = await db.execute(
        select(func.count()).select_from(Vendor).where(Vendor.is_active.is_(False))
    )
    # Distinct categories
    cat_r = await db.execute(select(func.count(func.distinct(Vendor.category))))

    recent_v = await db.execute(
        select(Vendor).order_by(Vendor.created_at.desc()).limit(10)
    )
    recent_vendors = [VendorRow(
        id=v.id, code=v.code, name=v.name, category=v.category,
        contact_name=v.contact_name, payment_terms=v.payment_terms,
        is_active=v.is_active, created_at=v.created_at,
    ) for v in recent_v.scalars()]

    inactive_count = inactive_r.scalar_one()
    return DashboardResponse(
        role="vendor_manager",
        kpis=[
            KpiCard(title="Total Vendors", value=str(total_r.scalar_one())),
            KpiCard(title="Active Vendors", value=str(active_r.scalar_one())),
            KpiCard(title="Inactive Vendors", value=str(inactive_count), alert=inactive_count > 0),
            KpiCard(title="Categories", value=str(cat_r.scalar_one())),
        ],
        recent_vendors=recent_vendors,
    )


async def build_system_admin(db: AsyncSession) -> DashboardResponse:
    total_r = await db.execute(select(func.count()).select_from(User))
    active_r = await db.execute(
        select(func.count()).select_from(User).where(User.is_active.is_(True))
    )
    dept_r = await db.execute(
        select(func.count()).select_from(
            __import__("app.models.department", fromlist=["Department"]).Department
        )
    )

    # Users by role
    role_result = await db.execute(
        select(User.role, func.count()).group_by(User.role).order_by(func.count().desc())
    )
    users_by_role = [RoleCount(role=row[0], count=row[1]) for row in role_result]

    return DashboardResponse(
        role="system_admin",
        kpis=[
            KpiCard(title="Total Users", value=str(total_r.scalar_one())),
            KpiCard(title="Active Users", value=str(active_r.scalar_one())),
            KpiCard(title="Departments", value=str(dept_r.scalar_one())),
            KpiCard(title="System Health", value="Healthy"),
        ],
        users_by_role=users_by_role,
    )


# ── Dispatch ─────────────────────────────────────────────────────────────────

# Roles that only ever exist as ADDITIONAL roles (identity's
# role_defs.assignable_as_primary = false) but still own a dashboard, in the
# order they win when the primary role has none. Without this, every builder
# below that names an additional-only role is unreachable code: the dispatcher
# reads the JWT's PRIMARY role, and these are never anyone's primary role.
_ADDITIONAL_ROLE_DASHBOARDS: tuple[str, ...] = ("payment_officer",)

# Primary roles whose dashboard is not simply the role name. Keep in sync with
# the branches in build() — this is only used to decide whether the primary role
# already owns a dashboard before falling back to additional roles.
_PRIMARY_WITH_DASHBOARD = frozenset({
    "dept_manager", "gm", "opm", "director", "supervisor", "dept_admin",
    "procurement_officer", "procurement_manager", "warehouse_staff", "ap_clerk",
    "finance_bp", "finance_manager", "cfo", "auditor", "vendor_manager",
    "system_admin",
})


async def build(db: AsyncSession, role: str, user_id: uuid.UUID) -> DashboardResponse:
    if role not in _PRIMARY_WITH_DASHBOARD:
        # The primary role has no dashboard of its own (requester, or a custom
        # role). An additional role may still carry one — this is what makes
        # payment_officer's dashboard reachable at all.
        eff = await _effective_roles(db, role, user_id)
        for code in _ADDITIONAL_ROLE_DASHBOARDS:
            if code in eff:
                role = code
                break
    # director / supervisor / dept_admin are scoped approvers: the EPMS frontend
    # has always routed them to the Approver dashboard, but they used to fall
    # through to build_requester here, so their "Pending Approvals" list was
    # structurally empty (the requester payload has no pending_approvals at all).
    if role in ("dept_manager", "gm", "opm", "director", "supervisor", "dept_admin"):
        return await build_approver(db, user_id, role)
    if role in ("procurement_officer", "procurement_manager"):
        return await build_procurement(db)
    if role == "warehouse_staff":
        return await build_warehouse(db)
    if role == "ap_clerk":
        return await build_ap_clerk(db)
    if role == "payment_officer":
        return await build_payment_officer(db)
    if role == "finance_bp":
        return await build_finance_bp(db)
    if role == "finance_manager":
        return await build_finance_manager(db, user_id, role)
    if role == "cfo":
        return await build_cfo(db)
    if role == "auditor":
        return await build_auditor(db)
    if role == "vendor_manager":
        return await build_vendor_manager(db)
    if role == "system_admin":
        return await build_system_admin(db)
    # Default: requester / unknown
    return await build_requester(db, user_id)
