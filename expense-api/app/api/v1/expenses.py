"""Expense claim endpoints — EXP / MIL / TRV / CFM (OA module)."""
import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.core.deps import BearerTokenDep, CurrentUserDep, SessionDep
from app.crud import expense as expense_crud
from app.schemas.expense import (
    ExpenseActionRequest,
    ExpenseClaimCreate,
    ExpenseClaimListResponse,
    ExpenseClaimResponse,
    ExpenseClaimUpdate,
    PaymentRecordRequest,
)
from app.services.approval_client import delegate_action
from app.services import finance_client

router = APIRouter(prefix="/expenses", tags=["expenses"])

# Roles that can trigger the pay action (kept for my_actions inbox logic)
_CAN_PAY = {"finance_bp", "finance_manager", "ap_clerk", "system_admin"}

# Step-index → role mapping for my_actions task inbox.
# These reflect the default workflow_defs["exp"] steps (dept_manager→finance_bp).
# If the workflow is customised this may drift; a future improvement would read
# from workflow_defs at request time.
_INBOX_STEP_ROLES: dict[int, set[str]] = {
    0: {"dept_manager", "system_admin"},
    1: {"finance_bp", "finance_manager", "system_admin"},
    2: {"finance_manager", "system_admin"},
}


def _action_key(claim_type: str) -> str:
    """Map claim_type to approval-api action key."""
    ct = claim_type.upper()
    mapping = {"EXP": "exp", "MIL": "mil", "TRV": "trv"}
    if ct in mapping:
        return mapping[ct]
    if ct.startswith("CFM"):
        # e.g. "CFM_TRAVEL" → "cfm_travel" for per-form workflow override
        code = ct.replace("CFM", "cfm", 1).lower()
        return code
    return "cfm"  # fallback


# Base workflow action key (for participation/step lookup in company_config.workflow_defs).
_BASE_WF_KEY = {"EXP": "exp", "MIL": "mil", "TRV": "trv"}


def _workflow_key(claim_type: str) -> str:
    ct = claim_type.upper()
    if ct.startswith("CFM"):
        return "cfm"
    return _BASE_WF_KEY.get(ct, "cfm")


async def _get_company_config(db):
    from sqlalchemy import select as sa_select
    from app.models.company_config_mirror import EpmsCompanyConfig
    return (await db.execute(sa_select(EpmsCompanyConfig).limit(1))).scalar_one_or_none()


async def _get_workflow_defs(db) -> dict:
    """Read action_key → steps[] from the EPMS company_config mirror (best-effort)."""
    cfg = await _get_company_config(db)
    return (cfg.workflow_defs or {}) if cfg else {}


async def _user_role_codes(db, user_id: uuid.UUID, base_role: str) -> set[str]:
    """Primary role + additional roles (identity user_roles, same DB). Replaces
    the retired company_config.role_management assignments (phase 3)."""
    codes = {base_role} if base_role else set()
    rows = (await db.execute(sa.text(
        "SELECT role_code FROM user_roles WHERE user_id = :u"), {"u": str(user_id)})).scalars().all()
    codes.update(rows)
    return codes


async def _can_act_on_claim(db, claim, user_id: uuid.UUID, role: str | None = None) -> bool:
    """Authoritative check: is the user the assigned approver for the claim's current
    open step? Reads the shared `tasks` table (written by approval-api) and resolves
    role-based tasks (assigned_user_id NULL) against the user's role union — JWT base
    role (if provided; otherwise looked up from `users.role`) plus any ADDITIONAL
    roles in identity's user_roles (same DB, phase 3 — replaces the retired
    company_config.role_management assignments).

    `assigned_role == "gm_or_opm"` is a synthetic name (not a real role_code): it is
    satisfied by a user holding EITHER the 'gm' or the 'opm' role.

    NOTE: pa.py imports this too — it is load-bearing for both expense claims and PAs.
    """
    from sqlalchemy import select as sa_select
    from app.models.task_mirror import TaskMirror
    open_tasks = list((await db.execute(
        sa_select(TaskMirror).where(
            TaskMirror.document_id == claim.id,
            TaskMirror.is_completed.is_(False),
        )
    )).scalars().all())
    if not open_tasks:
        return False

    codes: set[str] | None = None

    async def _codes() -> set[str]:
        nonlocal codes
        if codes is None:
            base_role = role
            if base_role is None:
                # Best-effort — no `users` mirror model exists in this service (see
                # get_approval_status below for the same raw-SQL pattern). A savepoint
                # keeps a missing/failed lookup from poisoning the outer transaction —
                # it just means the additional-role union has no base role in it.
                base_role = ""
                try:
                    async with db.begin_nested():
                        base_role = (await db.execute(sa.text(
                            "SELECT role FROM users WHERE id = :u"), {"u": str(user_id)})).scalar_one_or_none() or ""
                except Exception:
                    base_role = ""
            codes = await _user_role_codes(db, user_id, base_role)
        return codes

    for t in open_tasks:
        if t.assigned_user_id is not None and t.assigned_user_id == user_id:
            return True
        if t.assigned_user_id is None and t.assigned_role:
            assigned = t.assigned_role.lower()
            held = await _codes()
            if assigned == "gm_or_opm":
                if "gm" in held or "opm" in held:
                    return True
            elif assigned in held:
                return True
    return False


@router.get("", response_model=ExpenseClaimListResponse)
async def list_expenses(
    db: SessionDep,
    user: CurrentUserDep,
    claim_type: Annotated[str | None, Query(alias="type")] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    my_claims: bool = False,
    page: int = 1,
    page_size: Annotated[int, Query(le=100)] = 20,
):
    """OA expense list — role-based visibility (PRD §B):
    All roles see their own submissions + claims in their approval queue.
    system_admin sees all.
    """
    from sqlalchemy import func, or_, select as sa_select
    from app.models.expense import ExpenseClaim as EC
    from app.schemas.expense import ExpenseClaimListItem

    role = user.get("role", "")
    user_id = uuid.UUID(user["sub"])

    # Full visibility: system_admin (config) and ap_clerk (processes payments across all
    # claims — must keep seeing a claim after it is marked paid, not just while approved).
    if role in ("system_admin", "ap_clerk"):
        employee_id = user_id if my_claims else None
        items, total = await expense_crud.list_claims(
            db, claim_type=claim_type, status=status_filter,
            employee_id=employee_id, page=page, page_size=page_size,
        )
        return ExpenseClaimListResponse(
            items=[ExpenseClaimListItem.model_validate(c) for c in items],
            total=total,
        )

    # Req 1: visible to (a) the submitter and (b) everyone who participates in the
    # claim's approval workflow — i.e. any user whose role appears as a step in that
    # claim type's workflow_defs, for ALL non-draft claims (not just while it sits at
    # their step). This replaces the old "only while at my step" rule that made a claim
    # vanish from an approver's list the moment they approved it.
    wf = await _get_workflow_defs(db)

    def _roles_for(key: str) -> set[str]:
        return {s.get("role") for s in (wf.get(key) or [])}

    conditions = [EC.employee_id == user_id]  # own submissions (any status)

    type_conds = []
    for ct, key in (("EXP", "exp"), ("MIL", "mil"), ("TRV", "trv")):
        if role in _roles_for(key):
            type_conds.append(EC.claim_type == ct)
    if role in _roles_for("cfm"):
        type_conds.append(EC.claim_type.like("CFM%"))
    if type_conds:
        conditions.append(or_(*type_conds) & (EC.status != "draft"))

    # Pay roles also see approved claims (payment stage) even when not an approver step.
    if role in _CAN_PAY:
        conditions.append(EC.status == "approved")

    # Fallback: any claim the user personally acted on (covers cfm_<code> overrides and
    # workflow drift) — actor_id is recorded by approval-api in shared approval_events.
    from app.models.approval_event_mirror import ApprovalEventMirror as AEM
    acted_doc_ids = sa_select(AEM.document_id).where(AEM.actor_id == user_id)
    conditions.append(EC.id.in_(acted_doc_ids))

    q = sa_select(EC).where(or_(*conditions))
    if claim_type:
        q = q.where(EC.claim_type == claim_type)
    if status_filter:
        q = q.where(EC.status == status_filter)

    total = (await db.execute(
        sa_select(func.count()).select_from(q.subquery())
    )).scalar_one()
    paged = list((await db.execute(
        q.order_by(EC.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())

    return ExpenseClaimListResponse(
        items=[ExpenseClaimListItem.model_validate(c) for c in paged],
        total=total,
    )


@router.post("", response_model=ExpenseClaimResponse, status_code=status.HTTP_201_CREATED)
async def create_expense(
    body: ExpenseClaimCreate,
    db: SessionDep,
    user: CurrentUserDep,
):
    allowed = ("EXP", "MIL", "TRV")
    if body.claim_type not in allowed and not body.claim_type.startswith("CFM"):
        raise HTTPException(status_code=400, detail=f"claim_type must be one of {allowed} or CFM_<code>")

    user_id = uuid.UUID(user["sub"])
    user_name = user.get("full_name") or user.get("name") or user.get("email", "")
    dept_id_raw = user.get("department_id")
    dept_id = uuid.UUID(dept_id_raw) if dept_id_raw else None
    dept_name = user.get("department_name", "")

    claim = await expense_crud.create_claim(
        db,
        data=body,
        user_id=user_id,
        user_name=user_name,
        department_id=dept_id,
        department_name=dept_name,
    )
    return ExpenseClaimResponse.model_validate(claim)


@router.get("/my-actions", response_model=ExpenseClaimListResponse)
async def my_actions(db: SessionDep, user: CurrentUserDep):
    """Returns expense claims where the current user needs to take action.
    Used by Portal task inbox aggregation.
    """
    from sqlalchemy import or_, select
    from app.models.expense import ExpenseClaim as EC

    role = user.get("role", "")
    steps = _INBOX_STEP_ROLES.get(role, set()) if role in _INBOX_STEP_ROLES else set()

    conditions = []
    for step in steps:
        conditions.append(
            (EC.status.in_(["submitted", "in_review"])) &
            (EC.approval_step_idx == step)
        )
    if role in _CAN_PAY:
        conditions.append(EC.status == "approved")

    if not conditions:
        return ExpenseClaimListResponse(items=[], total=0)

    q = select(EC).where(or_(*conditions)).order_by(EC.created_at.desc()).limit(50)
    result = await db.execute(q)
    items = list(result.scalars().all())
    from app.schemas.expense import ExpenseClaimListItem
    return ExpenseClaimListResponse(
        items=[ExpenseClaimListItem.model_validate(c) for c in items],
        total=len(items),
    )


@router.get("/{claim_id}", response_model=ExpenseClaimResponse)
async def get_expense(claim_id: uuid.UUID, db: SessionDep, _: CurrentUserDep):
    claim = await expense_crud.get_by_id(db, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Expense claim not found")
    return ExpenseClaimResponse.model_validate(claim)


class ClaimPermissions(BaseModel):
    is_owner: bool
    can_approve: bool      # may approve / return / reject the current pending step
    can_pay: bool          # may record payment (status = approved)


@router.get("/{claim_id}/permissions", response_model=ClaimPermissions)
async def get_claim_permissions(claim_id: uuid.UUID, db: SessionDep, user: CurrentUserDep):
    """Whether the current user may act on this claim — resolved server-side against the
    shared tasks table plus the user's role union (JWT base role ∪ identity user_roles
    additional roles), since approval roles (Finance BP, etc.) are assignments, not JWT
    role claims."""
    claim = await expense_crud.get_by_id(db, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Expense claim not found")

    user_id = uuid.UUID(user["sub"])
    role = user.get("role", "")
    is_admin = role == "system_admin"
    is_owner = claim.employee_id == user_id

    can_approve = False
    if claim.status in ("submitted", "in_review") and not is_owner:
        can_approve = is_admin or await _can_act_on_claim(db, claim, user_id, role)

    can_pay = False
    if claim.status == "approved":
        codes = await _user_role_codes(db, user_id, role)
        can_pay = (
            is_admin
            or role in _CAN_PAY
            or "finance_bp" in codes
            or "finance_manager" in codes
        )

    return ClaimPermissions(is_owner=is_owner, can_approve=can_approve, can_pay=can_pay)


class ApprovalStepOut(BaseModel):
    step_idx: int
    role: str
    label: str
    state: str                       # waiting | current | approved | returned | rejected
    actor_name: str | None = None
    actor_role: str | None = None
    acted_at: str | None = None      # ISO timestamp
    action: str | None = None        # approve | return | reject
    comment: str | None = None


@router.get("/{claim_id}/approval-status", response_model=list[ApprovalStepOut])
async def get_approval_status(claim_id: uuid.UUID, db: SessionDep, _: CurrentUserDep):
    """Approval chain for a claim: each workflow step + who approved / who is pending.

    Steps come from company_config.workflow_defs (the configured chain); states are
    overlaid from the shared approval_events table (who acted, when) and the claim's
    current status / approval_step_idx.
    """
    from sqlalchemy import select as sa_select, text, bindparam
    from app.models.approval_event_mirror import ApprovalEventMirror as AEM

    claim = await expense_crud.get_by_id(db, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Expense claim not found")

    wf = await _get_workflow_defs(db)
    steps = wf.get(_action_key(claim.claim_type)) or wf.get(_workflow_key(claim.claim_type)) or []

    events = list((await db.execute(
        sa_select(AEM).where(AEM.document_id == claim_id).order_by(AEM.created_at.asc())
    )).scalars().all())
    approve_by_step = {e.step_idx: e for e in events if e.action == "approve"}
    terminal = next((e for e in events if e.action == "reject"), None)

    # Resolve actor display names from the shared users table (best-effort).
    names: dict = {}
    actor_ids = list({e.actor_id for e in events})
    if actor_ids:
        try:
            q = text("SELECT id, full_name FROM users WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True))
            names = {r[0]: r[1] for r in (await db.execute(q, {"ids": actor_ids})).all()}
        except Exception:
            names = {}

    current = claim.approval_step_idx
    status = claim.status
    out: list[ApprovalStepOut] = []
    for i, step in enumerate(steps):
        ev = approve_by_step.get(i)
        if status == "draft":
            state = "waiting"
        elif status in ("approved", "paid"):
            state = "approved"
        elif status == "rejected":
            if ev:
                state = "approved"
            elif terminal is not None and i == terminal.step_idx:
                state = "rejected"
            elif i < current:
                state = "approved"
            else:
                state = "waiting"
        elif status == "returned":
            if ev:
                state = "approved"
            elif i == current:
                state = "returned"
            elif i < current:
                state = "approved"
            else:
                state = "waiting"
        else:  # submitted / in_review
            if ev:
                state = "approved"
            elif i < current:
                state = "approved"   # auto-skipped or already cleared
            elif i == current:
                state = "current"
            else:
                state = "waiting"

        out.append(ApprovalStepOut(
            step_idx=i,
            role=step.get("role", ""),
            label=step.get("label") or step.get("role", f"Step {i + 1}"),
            state=state,
            actor_name=(names.get(ev.actor_id) if ev else None),
            actor_role=(ev.actor_role if ev else None),
            acted_at=(ev.created_at.isoformat() if ev else None),
            action=(ev.action if ev else None),
            comment=(ev.comment if ev else None),
        ))
    return out


@router.patch("/{claim_id}", response_model=ExpenseClaimResponse)
async def update_expense(
    claim_id: uuid.UUID,
    body: ExpenseClaimUpdate,
    db: SessionDep,
    user: CurrentUserDep,
):
    claim = await expense_crud.get_by_id(db, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Expense claim not found")
    user_id = uuid.UUID(user["sub"])
    if claim.employee_id != user_id and user.get("role") not in ("system_admin", "finance_manager"):
        raise HTTPException(status_code=403, detail="Cannot edit another employee's claim")
    try:
        await expense_crud.update_claim(db, claim, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.refresh(claim, ["line_items", "trip_items", "attachments", "approval_events"])
    return ExpenseClaimResponse.model_validate(claim)


@router.post("/{claim_id}/action", response_model=ExpenseClaimResponse)
async def expense_action(
    claim_id: uuid.UUID,
    body: ExpenseActionRequest,
    db: SessionDep,
    user: CurrentUserDep,
    token: BearerTokenDep,
):
    """Submit, approve, return, reject, recall, or cancel an expense claim.

    All workflow actions except 'pay' are delegated to approval-api, which
    reads workflow_defs[action_key] from CompanyConfig and updates
    expense_claims.status / approval_step_idx directly in the shared DB.
    """
    claim = await expense_crud.get_by_id(db, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Expense claim not found")

    action = body.action.lower()

    # EXP-007 / TRV-008: receipt-based claims require ≥1 attachment before submission.
    if action == "submit" and claim.claim_type in ("EXP", "TRV"):
        from sqlalchemy import func, select
        from app.models.expense import ExpenseAttachment
        att_count = (await db.execute(
            select(func.count()).select_from(ExpenseAttachment)
            .where(ExpenseAttachment.claim_id == claim_id)
        )).scalar_one()
        if att_count == 0:
            raise HTTPException(
                status_code=409,
                detail="At least one receipt attachment is required before submission",
            )

    # 'pay' is an expense-api concern (budget booking); handled locally.
    if action == "pay":
        raise HTTPException(
            status_code=400,
            detail="Use POST /expenses/{id}/pay to record payment",
        )

    # All other actions (submit / approve / return / reject / recall / cancel)
    # are delegated to approval-api which drives the configurable workflow.
    key = _action_key(claim.claim_type)
    try:
        await delegate_action(key, str(claim_id), action, body.comment, token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # approval-api wrote back to the shared DB; refresh to get updated state.
    await db.refresh(claim, ["line_items", "trip_items", "attachments", "approval_events"])
    return ExpenseClaimResponse.model_validate(claim)


@router.post("/{claim_id}/pay", response_model=ExpenseClaimResponse)
async def record_payment(
    claim_id: uuid.UUID,
    body: PaymentRecordRequest,
    db: SessionDep,
    user: CurrentUserDep,
    token: BearerTokenDep,
):
    """Record bank transfer for an approved expense claim.

    Pure forward (Phase a A0): finance-api's unified executor owns the whole
    payment — can_pay/SoD, status flip, payment_records, posting event, the
    OA audit row AND the idempotent budget booking. Nothing OA-side anymore.
    """
    claim = await expense_crud.get_by_id(db, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Expense claim not found")

    try:
        await finance_client.execute_payment(
            doc_kind="expense_claim", doc_id=claim_id, bearer_token=token,
            bank_account_id=body.bank_account_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    await db.refresh(claim, ["line_items", "trip_items", "attachments", "approval_events"])
    return ExpenseClaimResponse.model_validate(claim)
