"""CRUD + workflow for Purchase Request (PR)."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._numbering import next_number
from app.models.approval import ApprovalEvent
from app.models.config import CompanyConfig
from app.models.cost_center import CostCenter
from app.models.pr import PrLineItem, PurchaseRequest
from app.models.pr_attachment import PrAttachment
from app.models.task import Task
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.pr import ApprovalEventResponse, PrActionRequest, PrCreate, PrUpdate
from app.services import budget_client
from app.services.pdf_pr import generate_pr_pdf


# ── Number generation ──────────────────────────────────────────────────────────

async def _next_number(db: AsyncSession) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"PR-{today}-"
    return await next_number(db, PurchaseRequest.number, prefix, width=4)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _resolve_names(
    db: AsyncSession,
    vendor_id: uuid.UUID | None,
    cost_center_id: uuid.UUID | None,
    department_id: uuid.UUID | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Returns (vendor_name, cost_center_name, department_name).

    department_name is derived from the explicitly-selected department_id when
    present; falls back to the cost center's department for legacy callers.
    """
    vendor_name: str | None = None
    cost_center_name: str | None = None
    department_name: str | None = None

    if vendor_id:
        vendor = await db.get(Vendor, vendor_id)
        if vendor:
            vendor_name = vendor.name

    if cost_center_id:
        cc = await db.get(CostCenter, cost_center_id)
        if cc:
            cost_center_name = cc.name
            await db.refresh(cc, ["department"])
            if cc.department:
                department_name = cc.department.name  # fallback

    if department_id:
        from app.models.department import Department
        dept = await db.get(Department, department_id)
        if dept:
            department_name = dept.name  # explicit selection wins

    return vendor_name, cost_center_name, department_name


def _build_line_items(pr_id: uuid.UUID, items_in) -> list[PrLineItem]:
    result = []
    for i, item in enumerate(items_in):
        result.append(PrLineItem(
            pr_id=pr_id,
            description=item.description,
            material_id=item.material_id,
            supplier_item_id=item.supplier_item_id,
            qty=item.qty,
            unit=item.unit,
            unit_price=item.unit_price,
            line_total=item.line_total,
            notes=item.notes,
            sort_order=i,
        ))
    return result


def _sum_items(items_in) -> Decimal:
    return sum((item.line_total for item in items_in), Decimal("0"))


# ── Reads ──────────────────────────────────────────────────────────────────────

async def get_all(
    db: AsyncSession,
    *,
    status: str | None = None,
    pr_type: int | None = None,
    cost_center_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    is_prepaid: bool | None = None,
    created_by: uuid.UUID | None = None,
    pr_ids_subq=None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[PurchaseRequest], int]:
    q = select(PurchaseRequest)
    if pr_ids_subq is not None:
        q = q.where(PurchaseRequest.id.in_(pr_ids_subq))
    if status:
        q = q.where(PurchaseRequest.status == status)
    if pr_type:
        q = q.where(PurchaseRequest.type == pr_type)
    if cost_center_id:
        q = q.where(PurchaseRequest.cost_center_id == cost_center_id)
    if department_id:
        # PR carries cost_center_id; a department owns many cost centers.
        q = q.where(PurchaseRequest.cost_center_id.in_(
            select(CostCenter.id).where(CostCenter.department_id == department_id)
        ))
    if is_prepaid is not None:
        q = q.where(PurchaseRequest.is_prepaid == is_prepaid)
    if created_by:
        q = q.where(PurchaseRequest.created_by == created_by)
    if search:
        term = f"%{search}%"
        # Match the fields the UI advertises: PR#, title, vendor name, budget code.
        # Vendor name is matched two ways: the denormalized snapshot column
        # (covers imported/legacy PRs that carry a free-text vendor_name with no
        # vendor_id FK) AND a subquery on the live Vendor.name (covers renamed
        # vendors, keeps parity with the displayed name resolved via vendor_id).
        vendor_ids = select(Vendor.id).where(Vendor.name.ilike(term))
        q = q.where(
            PurchaseRequest.title.ilike(term)
            | PurchaseRequest.number.ilike(term)
            | PurchaseRequest.budget_code.ilike(term)
            | PurchaseRequest.vendor_name.ilike(term)
            | PurchaseRequest.vendor_id.in_(vendor_ids)
        )
    total: int = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    offset = (page - 1) * page_size
    items = list((await db.execute(q.order_by(PurchaseRequest.created_at.desc()).offset(offset).limit(page_size))).scalars().all())
    return items, total


async def get_by_id(db: AsyncSession, pr_id: uuid.UUID) -> PurchaseRequest | None:
    result = await db.execute(
        select(PurchaseRequest, User.full_name)
        .outerjoin(User, User.id == PurchaseRequest.created_by)
        .where(PurchaseRequest.id == pr_id)
    )
    row = result.first()
    if row is None:
        return None
    pr, creator_name = row
    pr.created_by_name = creator_name  # transient attr consumed by PrResponse
    return pr


# ── Create ─────────────────────────────────────────────────────────────────────

async def compute_budget_check(
    budget_code: str | None,
    cost_center_id: uuid.UUID | None,
    amount: Decimal,
    bearer_token: str | None = None,
) -> tuple[bool, Decimal | None]:
    """Single source of truth for PR over-budget determination.

    Returns (over_budget, available). `available` is the account's remaining
    balance, or None when it can't be determined (no budget_code / cost center,
    zero amount, or budget-api unreachable / account not found). Fail-open:
    over_budget is False whenever available is None so PR submission isn't
    blocked by budget-api downtime.

    Both the PR create path and the /pr/budget-check endpoint call this so the
    frontend and backend agree on over-budget without double-computing.
    """
    if not budget_code or cost_center_id is None or amount <= 0:
        return False, None
    fiscal_year = datetime.now(timezone.utc).year
    data = await budget_client.get_balance(
        bearer_token, cost_center_id, fiscal_year, account_code=budget_code,
    )
    if data is None:
        return False, None
    available = Decimal(str(data.get("available", 0)))
    return amount > available, available


async def _compute_over_budget(
    db: AsyncSession,  # noqa: ARG001 — kept for call-site stability
    budget_code: str | None,
    cost_center_id: uuid.UUID | None,
    pr_amount: Decimal,
    bearer_token: str | None = None,
) -> bool:
    """Return True if pr_amount would push the budget account's projected balance < 0.

    Thin wrapper over compute_budget_check (shared source of truth). Fail-open
    semantics: if budget-api is unreachable, returns False (allows PR to submit).
    """
    over_budget, _available = await compute_budget_check(
        budget_code, cost_center_id, pr_amount, bearer_token=bearer_token,
    )
    return over_budget


async def create(
    db: AsyncSession,
    payload: PrCreate,
    created_by: uuid.UUID,
    *, bearer_token: str | None = None,
) -> PurchaseRequest:
    number = await _next_number(db)
    vendor_name, cost_center_name, department_name = await _resolve_names(
        db, payload.vendor_id, payload.cost_center_id, payload.department_id
    )
    pr_amount = _sum_items(payload.line_items)
    over_budget = await _compute_over_budget(
        db, payload.budget_code, payload.cost_center_id, pr_amount,
        bearer_token=bearer_token,
    )
    pr = PurchaseRequest(
        number=number,
        title=payload.title,
        type=payload.type,
        currency=payload.currency,
        vendor_id=payload.vendor_id,
        vendor_name=vendor_name,
        is_prepaid=getattr(payload, "is_prepaid", False) or False,
        cost_center_id=payload.cost_center_id,
        cost_center_name=cost_center_name,
        department_id=payload.department_id,
        department_name=department_name,
        budget_code=payload.budget_code,
        factor_combo=payload.factor_combo,
        project_code=payload.project_code,
        required_by=payload.required_by,
        delivery_address=payload.delivery_address,
        notes=payload.notes,
        over_budget=over_budget,
        over_budget_justification=(
            payload.over_budget_justification if over_budget else None
        ),
        amount=pr_amount,
        created_by=created_by,
    )
    db.add(pr)
    await db.flush()  # get pr.id

    for item in _build_line_items(pr.id, payload.line_items):
        db.add(item)

    await db.flush()
    await db.refresh(pr)
    return pr


# ── Update (draft / returned only) ────────────────────────────────────────────

async def update(
    db: AsyncSession,
    pr: PurchaseRequest,
    payload: PrUpdate,
    *, bearer_token: str | None = None,
) -> PurchaseRequest:
    needs_name_refresh = False
    for field in ("title", "type", "currency", "vendor_id", "cost_center_id", "department_id",
                  "budget_code", "factor_combo", "project_code", "required_by", "delivery_address", "notes", "is_prepaid"):
        val = getattr(payload, field)
        if val is not None:
            setattr(pr, field, val)
            if field in ("vendor_id", "cost_center_id", "department_id"):
                needs_name_refresh = True
            if field == "factor_combo":
                # JSONB: flag the SQLAlchemy ORM that the dict mutated so the change is persisted.
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(pr, "factor_combo")
    if needs_name_refresh:
        vendor_name, cost_center_name, department_name = await _resolve_names(
            db, pr.vendor_id, pr.cost_center_id, pr.department_id
        )
        pr.vendor_name = vendor_name
        pr.cost_center_name = cost_center_name
        pr.department_name = department_name

    if payload.line_items is not None:
        # Replace all line items
        for old in list(pr.line_items):
            await db.delete(old)
        await db.flush()
        for item in _build_line_items(pr.id, payload.line_items):
            db.add(item)
        pr.amount = _sum_items(payload.line_items)

    # Recompute over_budget after potential changes to budget_code / cost_center / amount.
    pr.over_budget = await _compute_over_budget(
        db, pr.budget_code, pr.cost_center_id, pr.amount,
        bearer_token=bearer_token,
    )
    if payload.over_budget_justification is not None:
        pr.over_budget_justification = payload.over_budget_justification
    if not pr.over_budget:
        # Drop a stale justification once the PR is no longer over budget.
        pr.over_budget_justification = None

    await db.flush()
    await db.refresh(pr)
    return pr


# ── Workflow helpers ───────────────────────────────────────────────────────────

async def _get_config(db: AsyncSession) -> CompanyConfig | None:
    result = await db.execute(select(CompanyConfig).limit(1))
    return result.scalar_one_or_none()


async def _get_pr_workflow(db: AsyncSession) -> list[dict]:
    """Load PR workflow nodes from company config. Falls back to single dept_manager step."""
    cfg = await _get_config(db)
    if cfg and cfg.workflow_defs:
        nodes = cfg.workflow_defs.get("pr", [])
        if nodes:
            return nodes
    return [{"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"}]


# ── Workflow actions ───────────────────────────────────────────────────────────

async def action(
    db: AsyncSession,
    pr: PurchaseRequest,
    req: PrActionRequest,
    actor_id: uuid.UUID,
    actor_role: str,
) -> PurchaseRequest:
    """
    Valid transitions:
      submit   : draft | returned → submitted  (any authenticated)
      approve  : submitted | in_review → in_review | approved  (step role)
      return   : submitted | in_review → returned
      reject   : submitted | in_review → rejected
      cancel   : draft | returned | submitted → cancelled
    """
    act = req.action.lower()
    now = datetime.now(timezone.utc)
    step = pr.approval_step_idx
    cfg = await _get_config(db)
    workflow = (
        (cfg.workflow_defs.get("pr", []) if cfg and cfg.workflow_defs else [])
        or [{"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"}]
    )
    company_name = cfg.name if cfg else "EPMS"

    if act == "submit":
        if pr.status not in ("draft", "returned"):
            raise ValueError(f"Cannot submit PR in status '{pr.status}'")
        pr.status = "submitted"
        pr.submitted_at = now
        pr.approval_step_idx = 0
        # Create task for step-0 approver
        await _create_task(db, pr, step=0, workflow=workflow)

    elif act == "approve":
        if pr.status not in ("submitted", "in_review"):
            raise ValueError(f"Cannot approve PR in status '{pr.status}'")
        # Record the workflow step's role (not the JWT base role) so that
        # multi-role users appear correctly in the audit history.
        actor_role = workflow[step]["role"] if step < len(workflow) else actor_role
        # Complete current approval task
        await _complete_tasks(db, "pr", pr.id)

        # Build role → holder-set mapping for auto-skip logic. A post can be
        # held via PRIMARY role (users.role) OR an ADDITIONAL role (identity's
        # user_roles, same physical DB) — phase 3 retired the old single
        # company_config.role_management.<role>_user_id fields.
        from app.core.access_scope import role_holder_ids
        role_holders = await role_holder_ids(db)
        dept_manager_id = await _get_dept_manager_id(db, pr.created_by, "dept_manager")

        def _actor_holds_role_pr(role: str) -> bool:
            if role == "dept_manager":
                return dept_manager_id is not None and actor_id == dept_manager_id
            return actor_id in role_holders.get(role, set())

        next_step = step + 1
        # Auto-skip any consecutive steps where the same actor holds the assigned role.
        while next_step < len(workflow) and _actor_holds_role_pr(workflow[next_step]["role"]):
            auto_role = workflow[next_step]["role"]
            db.add(ApprovalEvent(
                document_type="pr",
                document_id=pr.id,
                document_number=pr.number,
                step_idx=next_step,
                action="approve",
                actor_id=actor_id,
                actor_role=auto_role,
                comment="Auto-approved (same approver holds both roles)",
            ))
            next_step += 1

        if next_step < len(workflow):
            pr.approval_step_idx = next_step
            pr.status = "in_review"
            await _create_task(db, pr, step=next_step, workflow=workflow)
        else:
            pr.status = "approved"
            await _attach_pr_pdf(db, pr, company_name)
            await _create_create_po_task(db, pr)

    elif act == "return":
        if pr.status not in ("submitted", "in_review"):
            raise ValueError(f"Cannot return PR in status '{pr.status}'")
        await _complete_tasks(db, "pr", pr.id)
        pr.status = "returned"
        pr.approval_step_idx = 0
        # Notify creator
        await _create_revise_task(db, pr)

    elif act == "reject":
        if pr.status not in ("submitted", "in_review"):
            raise ValueError(f"Cannot reject PR in status '{pr.status}'")
        await _complete_tasks(db, "pr", pr.id)
        pr.status = "rejected"

    elif act == "recall":
        if pr.status not in ("submitted", "in_review"):
            raise ValueError(f"Cannot recall PR in status '{pr.status}'")
        await _complete_tasks(db, "pr", pr.id)
        pr.status = "draft"
        pr.approval_step_idx = 0

    elif act == "cancel":
        if pr.status not in ("draft", "returned", "submitted"):
            raise ValueError(f"Cannot cancel PR in status '{pr.status}'")
        await _complete_tasks(db, "pr", pr.id)
        pr.status = "cancelled"

    else:
        raise ValueError(f"Unknown action '{act}'")

    # Record approval event
    db.add(ApprovalEvent(
        document_type="pr",
        document_id=pr.id,
        document_number=pr.number,
        step_idx=step,
        action=act,
        actor_id=actor_id,
        actor_role=actor_role,
        comment=req.comment,
    ))

    await db.flush()
    await db.refresh(pr)
    return pr


# ── PDF attachment helper ──────────────────────────────────────────────────────

async def _attach_pr_pdf(db: AsyncSession, pr: PurchaseRequest, company_name: str) -> None:
    """Generate an approved-PR PDF and store it as an attachment."""
    import asyncio
    loop = asyncio.get_event_loop()
    pdf_bytes = await loop.run_in_executor(None, generate_pr_pdf, pr, company_name)
    filename = f"{pr.number}.pdf"
    db.add(PrAttachment(
        pr_id=pr.id,
        filename=filename,
        content_type="application/pdf",
        file_size=len(pdf_bytes),
        file_data=pdf_bytes,
    ))


# ── Task helpers ───────────────────────────────────────────────────────────────

async def _get_dept_manager_id(
    db: AsyncSession,
    requester_id: uuid.UUID,
    role: str,
) -> uuid.UUID | None:
    """
    For dept_manager approval steps: find the manager in the same department
    as the requester.  Falls back to None (→ role-broadcast) if no match.
    """
    if role != "dept_manager":
        return None
    # Get requester's department_id
    result = await db.execute(
        select(User.department_id).where(User.id == requester_id)
    )
    dept_id = result.scalar_one_or_none()
    if not dept_id:
        return None
    # Find the active dept_manager in that same department
    result = await db.execute(
        select(User.id).where(
            User.role == "dept_manager",
            User.department_id == dept_id,
            User.is_active.is_(True),
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def _create_task(db: AsyncSession, pr: PurchaseRequest, step: int, workflow: list[dict]) -> None:
    wf = workflow[step]
    assigned_user_id = await _get_dept_manager_id(db, pr.created_by, wf["role"])
    db.add(Task(
        type="approve_pr",
        priority="normal",
        document_type="pr",
        document_id=pr.id,
        document_number=pr.number,
        assigned_role=wf["role"],
        assigned_user_id=assigned_user_id,
        title=f"Approve PR: {pr.number} — {pr.title}",
        description=f"Step {step + 1}/{len(workflow)}: {wf['label']} review required.",
        amount=pr.amount,
        vendor=pr.vendor_name,
    ))


async def _create_create_po_task(db: AsyncSession, pr: PurchaseRequest) -> None:
    """Create a 'Create PO' task for procurement officers after PR approval."""
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


async def _create_revise_task(db: AsyncSession, pr: PurchaseRequest) -> None:
    db.add(Task(
        type="revise_pr",
        priority="normal",
        document_type="pr",
        document_id=pr.id,
        document_number=pr.number,
        assigned_role="requester",
        assigned_user_id=pr.created_by,
        title=f"Revise PR: {pr.number} — {pr.title}",
        description="Your PR has been returned for revision.",
        amount=pr.amount,
        vendor=pr.vendor_name,
    ))


async def _complete_tasks(db: AsyncSession, doc_type: str, doc_id: uuid.UUID) -> None:
    result = await db.execute(
        select(Task).where(
            Task.document_type == doc_type,
            Task.document_id == doc_id,
            Task.is_completed.is_(False),
        )
    )
    now = datetime.now(timezone.utc)
    for task in result.scalars().all():
        task.is_completed = True
        task.completed_at = now


# ── Approval history ───────────────────────────────────────────────────────────

async def get_approval_events(
    db: AsyncSession, pr_id: uuid.UUID
) -> list[ApprovalEventResponse]:
    result = await db.execute(
        select(ApprovalEvent, User.full_name)
        .outerjoin(User, User.id == ApprovalEvent.actor_id)
        .where(ApprovalEvent.document_type == "pr", ApprovalEvent.document_id == pr_id)
        .order_by(ApprovalEvent.created_at)
    )
    return [
        ApprovalEventResponse(
            **{c: getattr(ev, c) for c in ApprovalEventResponse.model_fields if c != "actor_name"},
            actor_name=full_name,
        )
        for ev, full_name in result.all()
    ]
