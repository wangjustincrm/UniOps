"""OA Task List — aggregates pending actions from expense claims and PAs."""
import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.deps import CurrentUserDep, SessionDep

router = APIRouter(prefix="/tasks", tags=["tasks"])

# ── Types ─────────────────────────────────────────────────────────────────────

OaTaskType = Literal[
    "approve_expense",   # I am the next approver for this claim
    "approve_pa",        # I am the next approver for this PA
    "revise_expense",    # Returned to me — needs edit + resubmit
    "revise_pa",         # Returned to me — needs edit + resubmit
    "pay_expense",       # Approved claim waiting for payment recording
    "pay_pa",            # Approved PA waiting for payment recording
    "submitted_expense", # My own claim in-flight (informational)
    "submitted_pa",      # My own PA in-flight (informational)
]


class OaTaskItem(BaseModel):
    id: str               # unique task id: "exp-<uuid>" or "pa-<uuid>"
    task_type: str
    doc_type: str         # exp | mil | trv | cfm | pa | pa_dir
    doc_id: str
    doc_number: str
    title: str
    submitter_name: str
    amount: float
    currency: str
    status: str
    submitted_at: str | None
    created_at: str
    is_own: bool          # current user submitted this document


class OaTaskListResponse(BaseModel):
    items: list[OaTaskItem]
    total: int


# ── Role→step mappings ──────────────────────────────────────────────────────
# Derived at request time from company_config.workflow_defs (see _steps_from_wf
# / list_tasks below) instead of hardcoded step→role maps, so a customised
# approval flow stays consistent with the OA Task List.

_CAN_PAY = {"finance_bp", "finance_manager", "ap_clerk", "system_admin"}


def _steps_from_wf(wf: dict, *keys: str) -> dict[int, set[str]]:
    """{step_idx: {roles}} unioned across the given workflow_defs chains.
    Replaces hardcoded step→role maps so a customised approval flow stays consistent."""
    out: dict[int, set[str]] = {}
    for key in keys:
        for idx, step in enumerate(wf.get(key) or []):
            r = step.get("role")
            if r:
                out.setdefault(idx, set()).add(r)
    return out


def _exp_task_type(
    status: str,
    step_idx: int,
    is_own: bool,
    role: str,
    step_roles: dict[int, set[str]],
) -> str | None:
    if status == "returned" and is_own:
        return "revise_expense"
    if status == "approved" and role in _CAN_PAY:
        return "pay_expense"
    if status in ("submitted", "in_review") and not is_own:
        if role in step_roles.get(step_idx, set()) or role == "system_admin":
            return "approve_expense"
        return None
    if status in ("submitted", "in_review") and is_own:
        return "submitted_expense"
    return None


def _pa_task_type(
    status: str,
    step_idx: int,
    is_own: bool,
    role: str,
    step_roles: dict[int, set[str]],
) -> str | None:
    if status == "returned" and is_own:
        return "revise_pa"
    if status == "approved" and role in _CAN_PAY:
        return "pay_pa"
    if status in ("submitted", "in_review") and not is_own:
        if role in step_roles.get(step_idx, set()) or role == "system_admin":
            return "approve_pa"
        return None
    if status in ("submitted", "in_review") and is_own:
        return "submitted_pa"
    return None


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("", response_model=OaTaskListResponse)
async def list_tasks(db: SessionDep, user: CurrentUserDep):
    """Unified OA task list.

    Returns all pending OA items the current user has a role in:
    - Items in their approval queue
    - Their own submissions in-flight or returned
    - Approved items waiting for payment (finance / AP roles)
    """
    from sqlalchemy import or_, select
    from app.models.expense import ExpenseClaim as EC
    from app.models.pa import PaymentApplication as PA
    from app.api.v1.expenses import _get_workflow_defs

    role = user.get("role", "")
    user_id = uuid.UUID(user["sub"])
    wf = await _get_workflow_defs(db)
    exp_step_roles = _steps_from_wf(wf, "exp", "mil", "trv", "cfm")
    pa_step_roles = _steps_from_wf(wf, "pa", "pa_dir")
    tasks: list[OaTaskItem] = []

    # ── Expense claims ────────────────────────────────────────────────────────

    exp_conditions = [
        (EC.employee_id == user_id) & (EC.status.in_(["submitted", "in_review", "returned"])),
    ]
    for step, roles in exp_step_roles.items():
        if role in roles or role == "system_admin":
            exp_conditions.append(
                (EC.status.in_(["submitted", "in_review"])) & (EC.approval_step_idx == step)
            )
    if role in _CAN_PAY:
        exp_conditions.append(EC.status == "approved")

    exp_rows = list(
        (await db.execute(
            select(EC)
            .where(or_(*exp_conditions))
            .order_by(EC.created_at.desc())
            .limit(200)
        )).scalars().unique().all()
    )

    seen_exp: set[uuid.UUID] = set()
    for claim in exp_rows:
        if claim.id in seen_exp:
            continue
        is_own = claim.employee_id == user_id
        tt = _exp_task_type(claim.status, claim.approval_step_idx, is_own, role, exp_step_roles)
        if tt is None:
            continue
        seen_exp.add(claim.id)

        ct = claim.claim_type.upper()
        doc_type = {"EXP": "exp", "MIL": "mil", "TRV": "trv"}.get(ct, "cfm")

        tasks.append(OaTaskItem(
            id=f"exp-{claim.id}",
            task_type=tt,
            doc_type=doc_type,
            doc_id=str(claim.id),
            doc_number=claim.claim_number,
            title=claim.purpose or claim.notes or claim.claim_type,
            submitter_name=claim.employee_name,
            amount=float(claim.total_amount),
            currency=claim.currency,
            status=claim.status,
            submitted_at=claim.submitted_at.isoformat() if claim.submitted_at else None,
            created_at=claim.created_at.isoformat(),
            is_own=is_own,
        ))

    # ── Payment applications ──────────────────────────────────────────────────

    pa_conditions = [
        (PA.created_by == user_id) & (PA.status.in_(["submitted", "in_review", "returned"])),
    ]
    for step, roles in pa_step_roles.items():
        if role in roles or role == "system_admin":
            pa_conditions.append(
                (PA.status.in_(["submitted", "in_review"])) & (PA.approval_step_idx == step)
            )
    if role in _CAN_PAY:
        pa_conditions.append(PA.status == "approved")

    pa_rows = list(
        (await db.execute(
            select(PA)
            .where(or_(*pa_conditions))
            .order_by(PA.created_at.desc())
            .limit(200)
        )).scalars().unique().all()
    )

    seen_pa: set[uuid.UUID] = set()
    for pa in pa_rows:
        if pa.id in seen_pa:
            continue
        is_own = pa.created_by == user_id
        tt = _pa_task_type(pa.status, pa.approval_step_idx, is_own, role, pa_step_roles)
        if tt is None:
            continue
        seen_pa.add(pa.id)

        # Not `po_id is None`: an EPMS agreement PA has no PO either, and
        # labelling it pa_dir sends the deep link to OA's Direct-PA detail page.
        doc_type = "pa_dir" if pa.is_direct else "pa"

        tasks.append(OaTaskItem(
            id=f"pa-{pa.id}",
            task_type=tt,
            doc_type=doc_type,
            doc_id=str(pa.id),
            doc_number=pa.pa_number,
            title=pa.title or pa.vendor_name,
            submitter_name=pa.vendor_name,
            amount=float(pa.payment_amount),
            currency=pa.currency,
            status=pa.status,
            submitted_at=pa.submitted_at.isoformat() if pa.submitted_at else None,
            created_at=pa.created_at.isoformat(),
            is_own=is_own,
        ))

    tasks.sort(key=lambda t: t.created_at, reverse=True)
    return OaTaskListResponse(items=tasks, total=len(tasks))
