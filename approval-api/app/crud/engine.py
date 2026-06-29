"""Approval Engine — unified workflow execution for all UniOps modules.

Supports action keys: pr, po, pa, pa_dir, exp, mil, trv, cfm, cfm_<code>, vms_visit
Each action key binds to a configurable workflow stored in CompanyConfig.workflow_defs.
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget_plan import BudgetPlan
from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.expense import ExpenseClaim
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User
from app.models.visit import Visit as VmsVisit
from app.schemas.action import ActionResult


# ── status_attr indirection ───────────────────────────────────────────────────
# Per S2_ARCHITECTURE_REVIEW.md F1: each doc_type's `_DOC_META` entry can
# specify a `status_attr` field name; the engine reads/writes through that
# attribute instead of hardcoded `doc.status`. Default is `"status"` so the
# existing PR/PO/PA/EXP/MIL/TRV/CFM/BudgetPlan doc_types behave unchanged
# (`getattr(doc, "status")` is identical to `doc.status` by Python semantics).
def _status_of(meta: dict, doc: Any) -> Any:
    return getattr(doc, meta.get("status_attr", "status"))


def _set_status(meta: dict, doc: Any, value: str) -> None:
    setattr(doc, meta.get("status_attr", "status"), value)


# ── Per-action-key metadata ───────────────────────────────────────────────────

def _expense_meta(claim_type: str) -> dict:
    """Build _DOC_META entry for OA expense claim types (EXP / MIL / TRV / CFM*)."""
    code = claim_type.lower()
    return {
        "model":        ExpenseClaim,
        "number_attr":  "claim_number",
        "amount_attr":  "total_amount",
        "vendor_attr":  "employee_name",
        "task_approve": f"approve_{code}",
        "task_revise":  f"revise_{code}",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned"),
    }


_DOC_META: dict[str, dict] = {
    # ── EPMS procurement ──────────────────────────────────────────────────────
    "pr": {
        "model":        PurchaseRequest,
        "number_attr":  "number",
        "amount_attr":  "amount",
        "vendor_attr":  "vendor_name",
        "task_approve": "approve_pr",
        "task_revise":  "revise_pr",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned", "submitted"),
    },
    "po": {
        "model":        PurchaseOrder,
        "number_attr":  "number",
        "amount_attr":  "total",
        "vendor_attr":  "vendor_name",
        "task_approve": "approve_po",
        "task_revise":  "revise_po",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned", "submitted"),
    },
    # ── OA payment applications ───────────────────────────────────────────────
    "pa": {
        "model":        PaymentApplication,
        "number_attr":  "pa_number",
        "amount_attr":  "payment_amount",
        "vendor_attr":  "vendor_name",
        "task_approve": "approve_pa",
        "task_revise":  "revise_pa",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned"),
    },
    "pa_dir": {
        "model":        PaymentApplication,
        "number_attr":  "pa_number",
        "amount_attr":  "payment_amount",
        "vendor_attr":  "vendor_name",
        "task_approve": "approve_pa",
        "task_revise":  "revise_pa",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned"),
    },
    # ── OA expense claims ─────────────────────────────────────────────────────
    "exp": _expense_meta("EXP"),
    "mil": _expense_meta("MIL"),
    "trv": _expense_meta("TRV"),
    "cfm": _expense_meta("CFM"),
    # ── VMS visitor appointments (S2-B) ───────────────────────────────────────
    # Engine reads `approval_status`, not `status` — VMS owns a separate
    # `status: VisitStatus` enum for user-facing display. The post-approve
    # callback flips that column via raw SQL when all steps pass.
    "vms_visit": {
        "model":        VmsVisit,
        "number_attr":  "visit_title",   # populated by vms-api at submit
        "amount_attr":  None,            # visits have no money attached
        "vendor_attr":  None,
        "task_approve": "approve_vms_visit",
        "task_revise":  "revise_vms_visit",
        "status_attr":  "approval_status",
        "valid_submit":  ("draft",),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "submitted", "in_review"),
    },
    # ── budget-api plans ──────────────────────────────────────────────────────
    "budget_plan": {
        "model":        BudgetPlan,
        "number_attr":  "plan_number",   # synthetic property (BP-FYxxxx)
        "amount_attr":  "plan_amount",   # synthetic property (Decimal(0))
        "vendor_attr":  "cc_label",      # synthetic property (CC reference)
        "task_approve": "approve_budget_plan",
        "task_revise":  "revise_budget_plan",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned"),
    },
}


def _resolve_meta(doc_type: str) -> dict:
    """Return meta for known action keys; for cfm_<code> variants fall back to cfm."""
    if doc_type in _DOC_META:
        return _DOC_META[doc_type]
    if doc_type.startswith("cfm_"):
        return _expense_meta(doc_type.upper())
    raise KeyError(f"Unknown action key: {doc_type}")


# ── Config helpers ────────────────────────────────────────────────────────────

async def _get_config(db: AsyncSession) -> CompanyConfig | None:
    result = await db.execute(select(CompanyConfig).limit(1))
    return result.scalar_one_or_none()


async def _get_workflow(db: AsyncSession, doc_type: str) -> list[dict]:
    cfg = await _get_config(db)
    if cfg and cfg.workflow_defs:
        nodes = cfg.workflow_defs.get(doc_type, [])
        if nodes:
            return nodes
        # cfm_<code> falls back to the base cfm workflow if no per-form override
        if doc_type.startswith("cfm_"):
            nodes = cfg.workflow_defs.get("cfm", [])
            if nodes:
                return nodes
    return _WORKFLOW_DEFAULTS.get(doc_type) or _WORKFLOW_DEFAULTS.get(
        "cfm" if doc_type.startswith("cfm_") else doc_type, []
    )


# Default workflows shipped as PRD §3.3 seed values.
# These are only used as final fallback when CompanyConfig.workflow_defs is missing/empty.
# AE-2 seeds these into the DB on startup so this fallback is rarely reached.
_WORKFLOW_DEFAULTS: dict[str, list[dict]] = {
    "pr": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "gm_or_opm",   "role": "gm_or_opm",   "label": "GM / OPM"},
    ],
    "po": [
        {"id": "proc_mgr",  "role": "procurement_manager", "label": "Procurement Manager"},
        {"id": "gm_or_opm", "role": "gm_or_opm",           "label": "GM / OPM"},
    ],
    "pa": [
        {"id": "dept_manager",  "role": "dept_manager",   "label": "Department Manager"},
        {"id": "gm_or_opm",     "role": "gm_or_opm",      "label": "GM / OPM"},
        {"id": "finance_bp",    "role": "finance_bp",      "label": "Finance BP"},
        {"id": "finance_mgr",   "role": "finance_manager", "label": "Finance Manager"},
    ],
    "pa_dir": [
        {"id": "finance_bp",  "role": "finance_bp",      "label": "Finance BP"},
        {"id": "finance_mgr", "role": "finance_manager", "label": "Finance Manager"},
    ],
    "exp": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "finance_bp",   "role": "finance_bp",   "label": "Finance BP"},
    ],
    "mil": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "finance_bp",   "role": "finance_bp",   "label": "Finance BP"},
    ],
    "trv": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "finance_bp",   "role": "finance_bp",   "label": "Finance BP"},
    ],
    "cfm": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
    ],
    "budget_plan": [
        {"id": "dept_manager",   "role": "dept_manager",   "label": "Department Manager"},
        {"id": "finance_mgr",    "role": "finance_manager", "label": "Finance Manager"},
    ],
    "vms_visit": [
        # Default single-step. Admin adds a `quality_manager` step (W11 UI)
        # for GMP / Laboratory zones; vms-api resolves the actual user from
        # `vms_config.quality_manager_user_ids` and stores the chosen UUID
        # on `vms_visits.quality_approver_id` at submit time.
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
    ],
}


# ── Dept manager lookup ───────────────────────────────────────────────────────

async def _get_dept_manager_id(db: AsyncSession, requester_id: uuid.UUID) -> uuid.UUID | None:
    dept_result = await db.execute(select(User.department_id).where(User.id == requester_id))
    dept_id = dept_result.scalar_one_or_none()
    if not dept_id:
        return None
    mgr_result = await db.execute(
        select(User.id).where(
            User.role == "dept_manager",
            User.department_id == dept_id,
            User.is_active.is_(True),
        ).limit(1)
    )
    return mgr_result.scalar_one_or_none()


# ── Role map builder ──────────────────────────────────────────────────────────

def _build_role_map(rm: dict, dept_gm_opm: dict, department_id: uuid.UUID | None) -> dict[str, uuid.UUID | None]:
    def _uid(key: str) -> uuid.UUID | None:
        v = rm.get(key)
        return uuid.UUID(v) if v else None

    role_map: dict[str, uuid.UUID | None] = {
        "gm": _uid("gm_user_id"),
        "opm": _uid("opm_user_id"),
        "finance_manager": _uid("finance_manager_user_id"),
        "procurement_manager": _uid("procurement_manager_user_id"),
        "vendor_manager": _uid("vendor_manager_user_id"),
    }

    # gm_or_opm resolves via dept mapping
    if department_id:
        resolved = dept_gm_opm.get(str(department_id), "gm")
        role_map["gm_or_opm"] = role_map.get(resolved)
    else:
        role_map["gm_or_opm"] = role_map["gm"]

    return role_map


# ── Authorization check ───────────────────────────────────────────────────────

async def _actor_can_approve(
    db: AsyncSession,
    step_role: str,
    actor_id: uuid.UUID,
    actor_role: str,
    creator_id: uuid.UUID,
    rm: dict,
    dept_gm_opm: dict,
    finance_bp_ids: set[uuid.UUID],
    doc: Any | None = None,
) -> bool:
    """Return True if the actor is authorized to approve the current workflow step.

    `doc` is optional for back-compat (callers pre-S2 don't pass it). The
    VMS Quality Manager step needs it to read `doc.quality_approver_id`
    (per-visit assignment is VMS-local — no `role_management` mapping for
    quality_manager). See S2_ARCHITECTURE_REVIEW.md F2.
    """
    if actor_role == "system_admin":
        return True
    if step_role == "quality_manager":
        if doc is None:
            return False
        expected = getattr(doc, "quality_approver_id", None)
        return expected is not None and actor_id == expected
    if step_role == "finance_bp":
        return actor_id in finance_bp_ids
    if step_role == "dept_manager":
        dept_mgr_id = await _get_dept_manager_id(db, creator_id)
        return dept_mgr_id is not None and actor_id == dept_mgr_id
    if step_role == "gm_or_opm":
        # Resolve via creator's department — same logic as task assignment
        _, resolved_user_id = await _resolve_gm_or_opm(db, creator_id, rm, dept_gm_opm)
        return resolved_user_id is not None and actor_id == resolved_user_id
    # Named role: check role_management map
    uid_str = rm.get(f"{step_role}_user_id")
    if uid_str:
        return actor_id == uuid.UUID(uid_str)
    # Fallback: actor's own JWT role must match the step role (for broadcast steps)
    return actor_role == step_role


# ── Task helpers ──────────────────────────────────────────────────────────────

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


async def _resolve_gm_or_opm(
    db: AsyncSession,
    creator_id: uuid.UUID,
    rm: dict,
    dept_gm_opm: dict,
) -> tuple[str, uuid.UUID | None]:
    """Return (resolved_role, user_id) for a gm_or_opm step based on dept mapping."""
    dept_result = await db.execute(select(User.department_id).where(User.id == creator_id))
    dept_id = dept_result.scalar_one_or_none()

    resolved_role = dept_gm_opm.get(str(dept_id), "gm") if dept_id else "gm"
    uid_str = rm.get(f"{resolved_role}_user_id")
    return resolved_role, (uuid.UUID(uid_str) if uid_str else None)


async def _routing_user_id(db: AsyncSession, doc_type: str, doc: Any) -> uuid.UUID:
    """Return the user whose department drives department-based approval routing
    (the `dept_manager` and `gm_or_opm` steps).

    Routing must follow the *requester's* department — the person who raised the
    originating PR — NOT whoever created the PO/PA. POs are created by procurement
    officers and PAs by AP staff, so `doc.created_by` is the wrong department for
    these doc types. The chain is:
        po       → pr_id → PurchaseRequest.created_by
        pa/pa_dir → po_id → PurchaseOrder.pr_id → PurchaseRequest.created_by
    Falls back to `doc.created_by` when no PR is linked (direct PO / direct PA)
    and for every other doc_type (pr, exp, mil, trv, cfm*, budget_plan, vms_visit),
    where the creator IS the requester — so their behaviour is unchanged.
    """
    if doc_type == "po":
        pr_id = getattr(doc, "pr_id", None)
        if pr_id:
            uid = (await db.execute(
                select(PurchaseRequest.created_by).where(PurchaseRequest.id == pr_id)
            )).scalar_one_or_none()
            if uid:
                return uid
    elif doc_type in ("pa", "pa_dir"):
        po_id = getattr(doc, "po_id", None)
        if po_id:
            pr_id = (await db.execute(
                select(PurchaseOrder.pr_id).where(PurchaseOrder.id == po_id)
            )).scalar_one_or_none()
            if pr_id:
                uid = (await db.execute(
                    select(PurchaseRequest.created_by).where(PurchaseRequest.id == pr_id)
                )).scalar_one_or_none()
                if uid:
                    return uid
    return doc.created_by


async def _cc_label_for_plan(db: AsyncSession, doc: Any, fallback: str | None) -> str | None:
    """Resolve a human cost-center label ("CODE — Name") for a budget plan task.

    The BudgetPlan mirror only carries cost_center_id, so its `cc_label` property
    can only render "CC <uuid>". The cost_centers table (shared DB, owned by
    epms-api) holds code/name; look it up so the Task Inbox shows the name instead
    of a raw UUID. Falls back to whatever the mirror produced if the lookup misses.
    """
    cc_id = getattr(doc, "cost_center_id", None)
    if cc_id is None:
        return fallback
    row = (await db.execute(
        text("SELECT code, name FROM cost_centers WHERE id = CAST(:id AS uuid)"),
        {"id": str(cc_id)},
    )).first()
    if row is None:
        return fallback
    code, name = row
    return f"{code} — {name}" if name else (code or fallback)


async def _create_approve_task(
    db: AsyncSession,
    doc_type: str,
    doc: Any,
    step: int,
    workflow: list[dict],
    meta: dict,
    rm: dict,
    dept_gm_opm: dict,
    routing_uid: uuid.UUID,
) -> None:
    wf = workflow[step]
    role = wf["role"]
    assigned_user_id: uuid.UUID | None = None
    assigned_role = role

    if role == "dept_manager":
        # routing_uid = the requester (PR creator) — see _routing_user_id
        assigned_user_id = await _get_dept_manager_id(db, routing_uid)
        # dept_manager is a populous base JWT role. If we cannot resolve a
        # SPECIFIC manager (requester has no department, or that department has no
        # active dept_manager), we must NOT leave assigned_user_id=NULL: get_for_role
        # would broadcast the task to EVERY department manager company-wide, and
        # _actor_can_approve would let none of them act (PR-20260620-0001). Fail
        # loudly so the document stays put until the department is configured.
        if assigned_user_id is None:
            raise ValueError(
                "Cannot route approval: no active Department Manager is configured "
                "for the requester's department. Assign a Department Manager to that "
                "department (Admin → Departments / Users) before this document can "
                "be submitted."
            )
    elif role == "gm_or_opm":
        # GM vs OPM is decided by the requester's department, resolved from the
        # originating PR's creator (routing_uid), not the PO/PA creator.
        assigned_role, assigned_user_id = await _resolve_gm_or_opm(db, routing_uid, rm, dept_gm_opm)
    elif role == "quality_manager" and doc_type == "vms_visit":
        # VMS-local: vms-api pre-selected the QM and stored on the visit.
        # See S2_ARCHITECTURE_REVIEW.md F2.
        assigned_user_id = getattr(doc, "quality_approver_id", None)

    doc_number = getattr(doc, meta["number_attr"])
    amount = getattr(doc, meta["amount_attr"], None) if meta.get("amount_attr") else None
    vendor = getattr(doc, meta["vendor_attr"], None) if meta.get("vendor_attr") else None
    if doc_type == "budget_plan":
        vendor = await _cc_label_for_plan(db, doc, vendor)

    description = f"Step {step + 1}/{len(workflow)}: {wf['label']} review required."
    # OBG-002: enrich the over-budget pre-approval task with the requester's
    # justification so the approver sees it inline in the task inbox without
    # opening the PR detail page. Injected over-budget steps carry the ids
    # `ob_finance_manager` / `ob_gm_or_opm` (see execute_action — over-budget
    # injection block).
    if doc_type == "pr" and wf.get("id", "").startswith("ob_"):
        justification = (getattr(doc, "over_budget_justification", None) or "").strip()
        just_line = justification if justification else "(no justification provided)"
        description = (
            f"⚠️ OVER-BUDGET PRE-APPROVAL — PR amount: {amount}\n"
            f"Justification: {just_line}"
        )

    db.add(Task(
        type=meta["task_approve"],
        priority="normal",
        document_type=doc_type,
        document_id=doc.id,
        document_number=doc_number,
        assigned_role=assigned_role,
        assigned_user_id=assigned_user_id,
        title=f"Approve {doc_type.upper()}: {doc_number} — {doc.title}",
        description=description,
        amount=amount,
        vendor=vendor,
    ))


async def _create_revise_task(db: AsyncSession, doc_type: str, doc: Any, meta: dict) -> None:
    doc_number = getattr(doc, meta["number_attr"])
    amount = getattr(doc, meta["amount_attr"], None) if meta.get("amount_attr") else None
    vendor = getattr(doc, meta["vendor_attr"], None) if meta.get("vendor_attr") else None
    if doc_type == "budget_plan":
        vendor = await _cc_label_for_plan(db, doc, vendor)
    db.add(Task(
        type=meta["task_revise"],
        priority="normal",
        document_type=doc_type,
        document_id=doc.id,
        document_number=doc_number,
        assigned_role="requester",
        assigned_user_id=doc.created_by,
        title=f"Revise {doc_type.upper()}: {doc_number} — {doc.title}",
        description=f"Your {doc_type.upper()} has been returned for revision.",
        amount=amount,
        vendor=vendor,
    ))


# ── Post-approve side effects ─────────────────────────────────────────────────

async def _post_approve_pr(db: AsyncSession, pr: PurchaseRequest) -> None:
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


async def _post_approve_po(db: AsyncSession, po: PurchaseOrder) -> None:
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

    if po.is_prepaid:
        # Broadcast to all requesters — any requester can create the Prepayment PA.
        db.add(Task(
            type="create_prepayment_pa",
            priority="normal",
            document_type="po",
            document_id=po.id,
            document_number=po.number,
            assigned_role="requester",
            assigned_user_id=None,
            title=f"Create Prepayment PA: {po.number} — {po.title}",
            description=(
                f"PO {po.number} has been approved and requires an advance payment. "
                "Please create a Prepayment Payment Application before goods are delivered."
            ),
            amount=po.total,
            vendor=po.vendor_name,
        ))


async def _post_approve_pa(db: AsyncSession, pa: PaymentApplication) -> None:
    """PA-PO: notify AP Clerk. Invoices are marked paid only on the process action."""
    db.add(Task(
        type="process_pa",
        priority="normal",
        document_type="pa",
        document_id=pa.id,
        document_number=pa.pa_number,
        assigned_role="ap_clerk",
        title=f"Process Payment: {pa.pa_number} — {pa.title}",
        description=f"PA {pa.pa_number} has been fully approved. Please process the payment.",
        amount=pa.payment_amount,
        vendor=pa.vendor_name,
    ))


async def _post_approve_pa_dir(db: AsyncSession, pa: PaymentApplication) -> None:
    """PA-DIR: notify AP Clerk only — invoices are in expense_invoices (OA-owned), not EPMS."""
    db.add(Task(
        type="process_pa",
        priority="normal",
        document_type="pa_dir",
        document_id=pa.id,
        document_number=pa.pa_number,
        assigned_role="ap_clerk",
        title=f"Process Direct Payment: {pa.pa_number} — {pa.title}",
        description=f"Direct PA {pa.pa_number} has been fully approved. Please process the payment.",
        amount=pa.payment_amount,
        vendor=pa.vendor_name,
    ))


async def _post_approve_exp(db: AsyncSession, claim: ExpenseClaim) -> None:
    """Expense claim (EXP / MIL / TRV / CFM): notify Finance BP to reimburse the employee."""
    db.add(Task(
        type="process_expense",
        priority="normal",
        document_type=claim.claim_type.lower(),
        document_id=claim.id,
        document_number=claim.claim_number,
        assigned_role="finance_bp",
        title=f"Process Reimbursement: {claim.claim_number} — {claim.employee_name or 'Employee'}",
        description=f"{claim.claim_type} claim {claim.claim_number} has been approved. Please process the reimbursement.",
        amount=claim.total_amount,
        vendor=claim.employee_name,
    ))


async def _post_approve_budget_plan(db: AsyncSession, plan: BudgetPlan) -> None:
    """When a budget-plan revision is approved, flip is_current from parent → this row.

    - For v1 plans (parent_plan_id IS NULL): just ensure is_current=True.
    - For revisions (parent_plan_id is set): mark parent.is_current=False and
      this row .is_current=True. Old parent stays status='approved' for audit.
    """
    if plan.is_current:
        # Already current; nothing to do.
        return
    if plan.parent_plan_id is None:
        plan.is_current = True
        return
    parent = (await db.execute(
        select(BudgetPlan).where(BudgetPlan.id == plan.parent_plan_id)
    )).scalar_one_or_none()
    if parent is not None and parent.is_current:
        parent.is_current = False
    plan.is_current = True


async def _post_approve_vms_visit(db: AsyncSession, visit: VmsVisit) -> None:
    """Flip vms_visits.status (the VisitStatus enum) from pending_approval
    to confirmed once all approval steps have cleared.

    The engine writes `approval_status='approved'` via `_set_status` first;
    this callback then synchronizes the user-facing column so the rest of
    vms-api (badge-print guard, dashboard counters, etc.) sees the visit as
    ready to print.

    Implementation note: we use raw SQL because the thin Visit mirror in
    approval-api doesn't expose the `status` column at all — that column is
    owned by vms-api and uses an ENUM type we don't import here.
    """
    await db.execute(
        text("UPDATE vms_visits SET status = 'confirmed' WHERE id = :id"),
        {"id": visit.id},
    )


_POST_APPROVE: dict[str, Any] = {
    "pr":     _post_approve_pr,
    "po":     _post_approve_po,
    "pa":     _post_approve_pa,
    "pa_dir": _post_approve_pa_dir,
    "exp":    _post_approve_exp,
    "mil":    _post_approve_exp,
    "trv":    _post_approve_exp,
    "cfm":    _post_approve_exp,
    "budget_plan":  _post_approve_budget_plan,
    "vms_visit":    _post_approve_vms_visit,
}


# ── Main execution entry point ────────────────────────────────────────────────

async def execute_action(
    db: AsyncSession,
    doc_type: str,
    doc_id: uuid.UUID,
    action: str,
    actor_id: uuid.UUID,
    actor_role: str,
    comment: str | None = None,
) -> ActionResult:
    meta = _resolve_meta(doc_type)
    Model = meta["model"]

    result = await db.execute(select(Model).where(Model.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise LookupError(f"{doc_type.upper()} {doc_id} not found")

    act = action.lower()
    now = datetime.now(timezone.utc)
    step = doc.approval_step_idx
    workflow = await _get_workflow(db, doc_type)
    cfg = await _get_config(db)
    rm = cfg.role_management if cfg else {}
    dept_gm_opm = cfg.dept_gm_opm_mapping if cfg else {}

    # Department-based routing (dept_manager / gm_or_opm) follows the requester's
    # department — the originating PR creator — not the PO/PA creator. See
    # _routing_user_id. For PR and other doc types this is just doc.created_by.
    routing_uid = await _routing_user_id(db, doc_type, doc)

    # Over-budget pre-approval injection (PR only). Applies on every action so
    # that submit, approve, return, etc. all walk the same step list.
    # Mode is read from CompanyConfig.budget_admin_config.over_budget_mode
    # (single source of truth — see PRD §3.2.6 OBG-001~008).
    over_budget_mode = ""
    if doc_type == "pr" and getattr(doc, "over_budget", False):
        bac = (cfg.budget_admin_config if cfg else None) or {}
        over_budget_mode = bac.get("over_budget_mode", "fm_gm_opm")
        if over_budget_mode != "hard_block":
            prepend: list[dict] = [
                {"id": "ob_finance_manager", "role": "finance_manager",
                 "label": "Over-Budget — Finance Manager"},
            ]
            if over_budget_mode == "fm_gm_opm":
                prepend.append({"id": "ob_gm_or_opm", "role": "gm_or_opm",
                                "label": "Over-Budget — GM / OPM"})
            workflow = prepend + workflow

    doc_number = getattr(doc, meta["number_attr"])
    recorded_role = actor_role
    auto_skipped: list[int] = []

    if act == "submit":
        if _status_of(meta, doc) not in meta["valid_submit"]:
            raise ValueError(f"Cannot submit {doc_type.upper()} in status '{_status_of(meta, doc)}'")
        # hard_block: refuse over-budget PR submission outright (PRD OBG / Budget Config)
        if doc_type == "pr" and over_budget_mode == "hard_block":
            raise ValueError(
                "This PR exceeds the available budget for its account. "
                "Submission is blocked by Budget Config (hard_block mode). "
                "Reduce the budget code's commitment or split the PR into smaller items."
            )
        _set_status(meta, doc, "submitted")
        if hasattr(doc, "submitted_at"):
            doc.submitted_at = now
        doc.approval_step_idx = 0
        await _create_approve_task(db, doc_type, doc, step=0, workflow=workflow, meta=meta, rm=rm, dept_gm_opm=dept_gm_opm, routing_uid=routing_uid)

    elif act == "approve":
        if _status_of(meta, doc) not in meta["valid_approve"]:
            raise ValueError(f"Cannot approve {doc_type.upper()} in status '{_status_of(meta, doc)}'")

        # Authorization: verify the actor is the assigned approver for this step
        current_step_role = workflow[step]["role"] if step < len(workflow) else ""
        finance_bp_ids_auth = {uuid.UUID(u) for u in rm.get("finance_bp_user_ids", [])}
        authorized = await _actor_can_approve(
            db, current_step_role, actor_id, actor_role,
            routing_uid, rm, dept_gm_opm, finance_bp_ids_auth,
            doc=doc,
        )
        if not authorized:
            raise ValueError(
                f"Not authorized to approve this step (requires role: {current_step_role})"
            )

        recorded_role = workflow[step]["role"] if step < len(workflow) else actor_role
        await _complete_tasks(db, doc_type, doc.id)

        # Build role → user map for auto-skip
        # For gm_or_opm, resolve using the requester's department (routing_uid),
        # not doc.created_by (PO/PA creator) and not doc.department_id (None on PO).
        routing_dept_r = await db.execute(select(User.department_id).where(User.id == routing_uid))
        routing_dept_id = routing_dept_r.scalar_one_or_none()
        role_map = _build_role_map(rm, dept_gm_opm, routing_dept_id)
        finance_bp_ids = {uuid.UUID(u) for u in rm.get("finance_bp_user_ids", [])}
        dept_mgr_id = await _get_dept_manager_id(db, routing_uid)

        def _holds(role: str) -> bool:
            if role == "finance_bp":
                return actor_id in finance_bp_ids
            if role == "dept_manager":
                return dept_mgr_id is not None and actor_id == dept_mgr_id
            if role == "quality_manager" and doc_type == "vms_visit":
                # QM assignment is per-visit, not via role_map.
                qm_id = getattr(doc, "quality_approver_id", None)
                return qm_id is not None and actor_id == qm_id
            assigned = role_map.get(role)
            return assigned is not None and assigned == actor_id

        def _should_skip_step(role: str) -> tuple[bool, str]:
            """Return (skip, reason). Skip applies when the step is irrelevant
            for this particular document — currently only the VMS Quality
            Manager step on a visit that doesn't require QM review (no
            `quality_approver_id` populated by vms-api → access area is not
            GMP / Laboratory / All)."""
            if role == "quality_manager" and doc_type == "vms_visit":
                if getattr(doc, "quality_approver_id", None) is None:
                    return True, "Auto-skipped (access area does not require Quality Manager review)"
            return False, ""

        next_step = step + 1
        while next_step < len(workflow):
            next_role = workflow[next_step]["role"]
            # Conditional skip (per-document) — fires before the same-approver
            # check so non-GMP visits don't even check who holds the QM role.
            cond_skip, reason = _should_skip_step(next_role)
            if cond_skip:
                db.add(ApprovalEvent(
                    document_type=doc_type, document_id=doc.id, document_number=doc_number,
                    step_idx=next_step, action="approve",
                    actor_id=actor_id, actor_role=next_role,
                    comment=reason,
                ))
                auto_skipped.append(next_step)
                next_step += 1
                continue
            # Same-approver auto-skip (the existing optimisation)
            if _holds(next_role):
                db.add(ApprovalEvent(
                    document_type=doc_type, document_id=doc.id, document_number=doc_number,
                    step_idx=next_step, action="approve",
                    actor_id=actor_id, actor_role=next_role,
                    comment="Auto-approved (same approver holds both roles)",
                ))
                auto_skipped.append(next_step)
                next_step += 1
                continue
            break

        if next_step < len(workflow):
            doc.approval_step_idx = next_step
            _set_status(meta, doc, "in_review")
            await _create_approve_task(db, doc_type, doc, step=next_step, workflow=workflow, meta=meta, rm=rm, dept_gm_opm=dept_gm_opm, routing_uid=routing_uid)
        else:
            _set_status(meta, doc, "approved")
            if hasattr(doc, "approved_at"):
                doc.approved_at = now
            post_fn = _POST_APPROVE.get(doc_type)
            if post_fn is None and doc_type.startswith("cfm_"):
                post_fn = _post_approve_exp
            if post_fn:
                await post_fn(db, doc)

    elif act == "return":
        if _status_of(meta, doc) not in meta["valid_return"]:
            raise ValueError(f"Cannot return {doc_type.upper()} in status '{_status_of(meta, doc)}'")
        await _complete_tasks(db, doc_type, doc.id)
        _set_status(meta, doc, "returned")
        doc.approval_step_idx = 0
        await _create_revise_task(db, doc_type, doc, meta)

    elif act == "reject":
        if _status_of(meta, doc) not in meta["valid_return"]:
            raise ValueError(f"Cannot reject {doc_type.upper()} in status '{_status_of(meta, doc)}'")
        await _complete_tasks(db, doc_type, doc.id)
        # PA types use "cancelled" instead of "rejected" (consistent with PA state machine)
        _set_status(meta, doc, "cancelled" if doc_type in ("pa", "pa_dir") else "rejected")

    elif act == "recall":
        if _status_of(meta, doc) not in ("submitted", "in_review"):
            raise ValueError(f"Cannot recall {doc_type.upper()} in status '{_status_of(meta, doc)}'")
        if actor_role != "system_admin" and actor_id != doc.created_by:
            raise ValueError(f"Only the submitter can recall this {doc_type.upper()}")
        await _complete_tasks(db, doc_type, doc.id)
        _set_status(meta, doc, "draft")
        doc.approval_step_idx = 0

    elif act == "cancel":
        if _status_of(meta, doc) not in meta["valid_cancel"]:
            raise ValueError(f"Cannot cancel {doc_type.upper()} in status '{_status_of(meta, doc)}'")
        await _complete_tasks(db, doc_type, doc.id)
        _set_status(meta, doc, "cancelled")

    # NOTE (Phase 0-B1.5): the former "process" (payment) branch was removed.
    # Payment is not a workflow action — it is executed by finance-api's
    # unified executor (POST /finance/v1/payments/execute), which owns
    # can_pay, the status flip, payment_records, invoice marking and the
    # posting event. epms-api / expense-api forward their payment entries there.

    else:
        raise ValueError(f"Unknown action '{act}' for {doc_type.upper()}")

    # Record the primary approval event
    db.add(ApprovalEvent(
        document_type=doc_type, document_id=doc.id, document_number=doc_number,
        step_idx=step, action=act,
        actor_id=actor_id, actor_role=recorded_role,
        comment=comment,
    ))

    await db.flush()
    await db.refresh(doc)

    new_status = _status_of(meta, doc)
    return ActionResult(
        doc_type=doc_type,
        doc_id=doc.id,
        doc_number=doc_number,
        new_status=new_status,
        approval_step_idx=doc.approval_step_idx,
        workflow_complete=new_status in ("approved", "rejected", "cancelled", "processed"),
        auto_skipped_steps=auto_skipped,
    )
