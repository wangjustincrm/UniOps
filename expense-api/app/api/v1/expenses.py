"""Expense claim endpoints — EXP / MIL / TRV / CFM (OA module)."""
import logging
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
    ExpenseClaimListItem,
    ExpenseClaimListResponse,
    ExpenseClaimResponse,
    ExpenseClaimUpdate,
    PaymentRecordRequest,
)
from app.services.approval_client import delegate_action
from app.services.attachment_helper import delete_from_file_server
from app.services import finance_client

router = APIRouter(prefix="/expenses", tags=["expenses"])

logger = logging.getLogger(__name__)

# Roles that may VIEW payment-stage documents / OA payment attachments (kept for
# my_actions inbox logic, list visibility, and invoice-attachment read/delete).
# Intentionally fixed — payment authority is a hardcoded financial-role set,
# not part of the configurable approval workflow (unlike the step→role
# lookups in my_actions below, which are derived from workflow_defs).
#
# NOTE this is a VISIBILITY set, not the payment-execution gate — it still
# includes ap_clerk (2026-08-13: AP Clerk keeps read access to payment-stage
# docs/attachments after payment_officer took over execution; see finance-api's
# _FINANCE_ROLES in app/core/deps.py, which is the same split). Do not remove
# ap_clerk from here to "match" the pay gate below — that silently blinds AP.
_CAN_PAY = {"finance_bp", "finance_manager", "ap_clerk", "payment_officer", "system_admin"}

# Who may actually EXECUTE the pay action — mirrors finance-api's authoritative
# gate (_PAY_ROLES / _PAY_ROLES_ASSIGNED in app/crud/payment_execute.py) so this
# service's can_pay flag never grants a button finance-api's endpoint will 403,
# nor hides one that endpoint would allow. ap_clerk is deliberately NOT in this
# set — payment execution moved to payment_officer; system_admin is deliberately
# primary-role-only (this codebase's convention, see budget_scope.py's
# FULL_ACCESS_PRIMARY vs FULL_ACCESS_ASSIGNED).
_PAY_PRIMARY = {"payment_officer", "finance_manager", "finance_bp", "system_admin"}
_PAY_ASSIGNED = _PAY_PRIMARY - {"system_admin"}


def _action_key(claim_type: str) -> str:
    """Map claim_type to approval-api action key."""
    ct = claim_type.upper()
    mapping = {"EXP": "exp", "MIL": "mil", "TRV": "trv", "TRA": "tra"}
    if ct in mapping:
        return mapping[ct]
    if ct.startswith("CFM"):
        # e.g. "CFM_TRAVEL" → "cfm_travel" for per-form workflow override
        code = ct.replace("CFM", "cfm", 1).lower()
        return code
    return "cfm"  # fallback


# Base workflow action key (for participation/step lookup in company_config.workflow_defs).
_BASE_WF_KEY = {"EXP": "exp", "MIL": "mil", "TRV": "trv", "TRA": "tra"}


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
    """PRIMARY role + ADDITIONAL roles from identity's user_roles.

    Delegates to core.authz_matrix so there is ONE definition of "which roles
    does this user hold" in the service. The three local copies this replaced
    all read user_roles bare, without joining role_defs — so a role an admin
    had DEACTIVATED still granted OA approval rights and visibility to everyone
    holding it, while the matrix helper (used by invoice_attachments) correctly
    ignored it. Same table, two answers.
    """
    from app.core.authz_matrix import user_role_codes
    return await user_role_codes(db, user_id, base_role)


async def approvable_document_ids(db, user_id: uuid.UUID, role: str) -> set[uuid.UUID]:
    """Documents this user currently has an OPEN approve task on.

    The one definition of "is this mine to approve", used by the task inbox
    (my_actions), the OA task list (tasks.py) and the list endpoints below.
    It had been reimplemented three times, and the copies drifted — which is
    how the OA task list ended up with no department scope, no role union and
    no delegation months after my_actions had all three.

    Resolves the same way `_can_act_on_claim` does, so "it is in my list" and
    "the Approve button works" cannot disagree:
      * a task pinned to me;
      * a broadcast task (no assignee) whose role is in my role union, with
        `gm_or_opm` satisfied by holding either post;
      * the same two for anyone who has named me their stand-in today.
    """
    from sqlalchemy import and_, func, or_, select
    from app.core.delegation import active_delegator_ids, delegated_broadcast_roles
    from app.models.task_mirror import TaskMirror as TM

    roles = await _user_role_codes(db, user_id, role)
    assigned_roles = {r.lower() for r in roles}
    if "gm" in assigned_roles or "opm" in assigned_roles:
        assigned_roles.add("gm_or_opm")

    delegator_ids = await active_delegator_ids(db, user_id)
    deleg_roles = {r.lower() for r in await delegated_broadcast_roles(db, delegator_ids)}
    if "gm" in deleg_roles or "opm" in deleg_roles:
        deleg_roles.add("gm_or_opm")

    arms = [
        TM.assigned_user_id == user_id,
        and_(TM.assigned_user_id.is_(None), func.lower(TM.assigned_role).in_(assigned_roles)),
    ]
    if delegator_ids:
        arms.append(TM.assigned_user_id.in_(delegator_ids))
        if deleg_roles:
            arms.append(and_(TM.assigned_user_id.is_(None),
                             func.lower(TM.assigned_role).in_(deleg_roles)))

    return set((await db.execute(
        select(TM.document_id).where(
            TM.is_completed.is_(False),
            TM.type.like("approve_%"),
            or_(*arms),
        )
    )).scalars().all())


async def acted_on_document_ids(db, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Documents this user has personally acted on, from shared approval_events.

    The other half of "an approver sees what they approve": once they have
    approved something the task closes, and without this the document would
    drop out of their list the moment they touched it.
    """
    from sqlalchemy import select
    from app.models.approval_event_mirror import ApprovalEventMirror as AEM
    return set((await db.execute(
        select(AEM.document_id).where(AEM.actor_id == user_id)
    )).scalars().all())


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

    # Delegation (task-11): a delegate standing in for one or more delegators
    # today may act on any of the delegators' open approve tasks — pinned
    # directly to a delegator, or role-pool tasks for a role a delegator
    # holds. This function sees every open task on the document (unlike
    # my-actions' TM.type.like("approve_%") predicate), so the approve-type
    # guard is applied here explicitly.
    from app.core.delegation import active_delegator_ids, delegated_broadcast_roles
    delegator_ids = await active_delegator_ids(db, user_id)
    if delegator_ids:
        deleg_roles = await delegated_broadcast_roles(db, delegator_ids)
        for t in open_tasks:
            if not (t.type or "").startswith("approve"):
                continue
            if t.assigned_user_id is not None and t.assigned_user_id in delegator_ids:
                return True
            if t.assigned_user_id is None and t.assigned_role:
                assigned = t.assigned_role.lower()
                held = {r.lower() for r in deleg_roles}
                if "gm" in held or "opm" in held:
                    held.add("gm_or_opm")
                if assigned in held:
                    return True
    return False


_DELETABLE_STATUSES = ("draft", "returned", "submitted", "in_review")


def can_delete_claim(claim, user_id: uuid.UUID, role: str) -> bool:
    """Whether `user_id` may hard-delete `claim`. Pure — no queries.

    Deliberately does not check for a referencing TRV: that would be one query
    per row on every list render, for a case the status rule already makes
    unreachable. The DELETE endpoint runs that check.
    """
    if claim.claim_type != "TRA":
        return False
    if claim.status not in _DELETABLE_STATUSES:
        return False
    return role == "system_admin" or claim.employee_id == user_id


def _list_item(claim, user_id: uuid.UUID, role: str) -> ExpenseClaimListItem:
    """Serialize one list row, stamping the server-computed delete permission."""
    item = ExpenseClaimListItem.model_validate(claim)
    item.can_delete = can_delete_claim(claim, user_id, role)
    return item


@router.get("", response_model=ExpenseClaimListResponse)
async def list_expenses(
    db: SessionDep,
    user: CurrentUserDep,
    claim_type: Annotated[str | None, Query(alias="type")] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    exclude_type: Annotated[str | None, Query(alias="exclude_type")] = None,
    my_claims: bool = False,
    page: int = 1,
    page_size: Annotated[int, Query(le=100)] = 20,
):
    """OA expense list — role-based visibility (PRD §B):
    All roles see their own submissions + claims in their approval queue.
    system_admin sees all.

    `exclude_type` (comma-separated) drops claim types from the result. The
    Expense Claims page sends `exclude_type=TRA`: a Travel Application shares
    this table but is not a reimbursement (total_amount 0, never reaches the
    payment path, and its detail route is /travel/:id) — without this it showed
    up on both /expenses and /travel. The Travel Applications page sends
    `type=TRA` instead and is unaffected.
    """
    from sqlalchemy import func, or_, select as sa_select
    from app.models.expense import ExpenseClaim as EC
    from app.schemas.expense import ExpenseClaimListItem

    role = user.get("role", "")
    user_id = uuid.UUID(user["sub"])
    excluded = [t.strip() for t in exclude_type.split(",") if t.strip()] if exclude_type else []

    # Full visibility: system_admin (config) and ap_clerk (processes payments across all
    # claims — must keep seeing a claim after it is marked paid, not just while approved).
    if role in ("system_admin", "ap_clerk"):
        employee_id = user_id if my_claims else None
        items, total = await expense_crud.list_claims(
            db, claim_type=claim_type, status=status_filter,
            employee_id=employee_id, exclude_types=excluded,
            page=page, page_size=page_size,
        )
        return ExpenseClaimListResponse(
            items=[_list_item(c, user_id, role) for c in items],
            total=total,
        )

    # An employee sees the documents they raised; an approver sees the documents
    # they approve. Nothing else. (Business rule, 2026-09-11.)
    #
    # The previous rule was far wider: if the caller's role appeared ANYWHERE in
    # a claim type's workflow_defs, they saw every non-draft claim of that type,
    # company-wide. dept_manager is a populous role, so in practice every
    # department manager could read every expense claim in the business —
    # requester name, purpose and amount. It was written to fix something real
    # (a claim vanished from an approver's list the moment they approved it),
    # but the fix was much broader than the problem, and the narrow answer was
    # already sitting in the same function: approval_events.
    #
    # Both halves of "the documents they approve" are needed — the open task
    # covers what is waiting for them now, approval_events covers what they have
    # already dealt with.
    approvable = await approvable_document_ids(db, user_id, role)
    acted = await acted_on_document_ids(db, user_id)

    conditions = [EC.employee_id == user_id]          # raised by me, any status
    if approvable:
        conditions.append(EC.id.in_(approvable))      # waiting for me to approve
    if acted:
        conditions.append(EC.id.in_(acted))           # I have acted on it

    # Payment is a stage of the document's life, not an approval step, and it is
    # keyed on a role pool rather than a per-document task — so the finance
    # roles that settle claims need to see approved ones to do it. TRA excluded:
    # total_amount is 0 and it never enters the payment path.
    if role in _CAN_PAY:
        conditions.append((EC.status == "approved") & (EC.claim_type != "TRA"))

    q = sa_select(EC).where(or_(*conditions))
    if claim_type:
        q = q.where(EC.claim_type == claim_type)
    if excluded:
        q = q.where(EC.claim_type.not_in(excluded))
    if status_filter:
        q = q.where(EC.status == status_filter)

    total = (await db.execute(
        sa_select(func.count()).select_from(q.subquery())
    )).scalar_one()
    paged = list((await db.execute(
        q.order_by(EC.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())

    return ExpenseClaimListResponse(
        items=[_list_item(c, user_id, role) for c in paged],
        total=total,
    )


@router.post("", response_model=ExpenseClaimResponse, status_code=status.HTTP_201_CREATED)
async def create_expense(
    body: ExpenseClaimCreate,
    db: SessionDep,
    user: CurrentUserDep,
):
    allowed = ("EXP", "MIL", "TRV", "TRA")
    if body.claim_type not in allowed and not body.claim_type.startswith("CFM"):
        raise HTTPException(status_code=400, detail=f"claim_type must be one of {allowed} or CFM_<code>")

    user_id = uuid.UUID(user["sub"])
    user_name = user.get("full_name") or user.get("name") or ""
    dept_id_raw = user.get("department_id")
    dept_id = uuid.UUID(dept_id_raw) if dept_id_raw else None
    dept_name = user.get("department_name") or ""

    # The access token carries only {sub, role, type} — full_name / department are
    # NOT in it (identity-api create_access_token). Resolve them from the shared
    # users/departments tables so the claim records a real employee & department;
    # otherwise the Expense Claims list shows a blank Employee column (prod claims
    # EXP-20260729-0001/0002, 2026-08-03). Best-effort — never block creation if
    # the identity tables are unavailable (mirrors the actor-name lookup below).
    if not user_name or not dept_name or dept_id is None:
        try:
            # SAVEPOINT so a failed lookup (e.g. identity tables absent) rolls
            # back cleanly and never poisons the outer create transaction.
            async with db.begin_nested():
                row = (await db.execute(
                    sa.text(
                        "SELECT u.full_name, u.department_id, d.name AS department_name "
                        "FROM users u LEFT JOIN departments d ON d.id = u.department_id "
                        "WHERE u.id = :uid"
                    ),
                    {"uid": user_id},
                )).first()
        except Exception:
            row = None
        if row is not None:
            user_name = user_name or (row.full_name or "")
            if dept_id is None:
                dept_id = row.department_id
            dept_name = dept_name or (row.department_name or "")

    user_name = user_name or user.get("email", "")

    # TRV reimbursement gate: must reference an APPROVED TRA the user travels on.
    if body.claim_type == "TRV":
        if not body.travel_application_id:
            raise HTTPException(status_code=400,
                detail="A Travel Application is required for travel expense claims")
        tra = await expense_crud.get_by_id(db, body.travel_application_id)
        if not tra or tra.claim_type != "TRA":
            raise HTTPException(status_code=404, detail="Travel Application not found")
        if tra.status != "approved":
            raise HTTPException(status_code=400,
                detail="The selected Travel Application is not approved yet")
        if user_id not in {t.user_id for t in tra.travelers}:
            raise HTTPException(status_code=403,
                detail="You are not listed as a traveler on this Travel Application")

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

    The approval half reads the shared `tasks` table (written by approval-api) —
    the same source `_can_act_on_claim` gates the Approve button on, so the inbox
    lists exactly what the caller can actually act on. It used to select every
    claim parked at a workflow step whose role the caller holds, which had no
    department predicate at all: since `dept_manager` is a populous role, every
    department manager saw every company claim at that step (requester name and
    amount included). approval-api PINS a dept_manager task to the one manager
    who routes for that claim's department, so reading tasks restores the scope
    without this endpoint having to re-derive routing rules of its own.

    Trade-off, deliberate: a claim whose approval task was closed while the claim
    stayed open (the Mark Done incident) no longer appears here. It cannot be
    approved by anyone — it needs the heal script, not an inbox row.

    The payment half (approved claims for _CAN_PAY roles) is a role pool with no
    per-document task, so it stays role-based.
    """
    from sqlalchemy import and_, func, or_, select
    from app.core.delegation import active_delegator_ids, delegated_broadcast_roles
    from app.models.expense import ExpenseClaim as EC
    from app.models.task_mirror import TaskMirror as TM

    role = user.get("role", "")
    user_id = uuid.UUID(user["sub"])
    roles = await _user_role_codes(db, user_id, role)   # multi-role union

    # `gm_or_opm` is a synthetic assigned_role (not a real role code): approval-api
    # broadcasts singleton-post steps under it so the CURRENT holder resolves live.
    # Mirrors _can_act_on_claim.
    assigned_roles = {r.lower() for r in roles}
    if "gm" in assigned_roles or "opm" in assigned_roles:
        assigned_roles.add("gm_or_opm")

    # Delegation (task-11): fold in any live delegators' pinned tasks and the
    # role-pool tasks for roles they hold. Only widens which TASKS are
    # matched here — never the caller's own role/visibility scope above.
    delegator_ids = await active_delegator_ids(db, user_id)
    deleg_roles = {r.lower() for r in await delegated_broadcast_roles(db, delegator_ids)}
    if "gm" in deleg_roles or "opm" in deleg_roles:
        deleg_roles.add("gm_or_opm")

    arms = [
        TM.assigned_user_id == user_id,
        and_(TM.assigned_user_id.is_(None),
             func.lower(TM.assigned_role).in_(assigned_roles)),
    ]
    if delegator_ids:
        arms.append(TM.assigned_user_id.in_(delegator_ids))
        if deleg_roles:
            arms.append(and_(TM.assigned_user_id.is_(None),
                              func.lower(TM.assigned_role).in_(deleg_roles)))

    open_task_for_me = select(TM.document_id).where(
        TM.is_completed.is_(False),
        TM.type.like("approve_%"),
        or_(*arms),
    )

    conditions = [
        EC.status.in_(["submitted", "in_review"]) & EC.id.in_(open_task_for_me)
    ]

    # TRA excluded — an approved Travel Application has total_amount 0 and never
    # enters the payment path, so it must not surface as a pay-action inbox item.
    if any(r in _CAN_PAY for r in roles):
        conditions.append((EC.status == "approved") & (EC.claim_type != "TRA"))

    q = select(EC).where(or_(*conditions)).order_by(EC.created_at.desc()).limit(50)
    result = await db.execute(q)
    items = list(result.scalars().all())
    from app.schemas.expense import ExpenseClaimListItem
    return ExpenseClaimListResponse(
        items=[ExpenseClaimListItem.model_validate(c) for c in items],
        total=len(items),
    )


async def _can_view_claim(db, claim, user_id: uuid.UUID, role: str) -> bool:
    """Object-level read gate — the same rule list_expenses filters on.

    It has to be the same rule, or the list is decoration: a claim the list
    withholds but the detail endpoint serves is still readable by anyone who
    can guess a URL. The role-appears-in-workflow_defs test that used to sit
    here was exactly that hole — it let any holder of any step role read any
    claim of that type, company-wide.
    """
    if claim.employee_id == user_id or role == "system_admin":
        return True
    # Finance/AP settle claims they never approved; they need to read them.
    if role in _CAN_PAY:
        return True
    from sqlalchemy import select as sa_select, func as sa_func
    from app.models.approval_event_mirror import ApprovalEventMirror as AEM
    acted = (await db.execute(
        sa_select(sa_func.count()).select_from(AEM).where(
            AEM.document_id == claim.id, AEM.actor_id == user_id
        )
    )).scalar_one()
    if acted:
        return True
    return await _can_act_on_claim(db, claim, user_id, role)


@router.get("/{claim_id}", response_model=ExpenseClaimResponse)
async def get_expense(claim_id: uuid.UUID, db: SessionDep, user: CurrentUserDep):
    claim = await expense_crud.get_by_id(db, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Expense claim not found")
    if not await _can_view_claim(db, claim, uuid.UUID(user["sub"]), user.get("role", "")):
        raise HTTPException(status_code=403, detail="Not authorized to view this expense claim")
    return ExpenseClaimResponse.model_validate(claim)


class ClaimPermissions(BaseModel):
    is_owner: bool
    can_approve: bool      # may approve / return / reject the current pending step
    can_pay: bool          # may record payment (status = approved)
    can_delete: bool = False   # may hard-delete (unapproved Travel Applications only)


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
    if claim.status == "approved" and claim.claim_type != "TRA":
        codes = await _user_role_codes(db, user_id, role)
        # Mirrors finance-api's authoritative gate (_PAY_ROLES / _PAY_ROLES_ASSIGNED)
        # — NOT _CAN_PAY, which is a visibility set and still includes ap_clerk.
        can_pay = role in _PAY_PRIMARY or bool(codes & _PAY_ASSIGNED)

    return ClaimPermissions(
        is_owner=is_owner,
        can_approve=can_approve,
        can_pay=can_pay,
        can_delete=can_delete_claim(claim, user_id, role),
    )


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
async def get_approval_status(claim_id: uuid.UUID, db: SessionDep, user: CurrentUserDep):
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

    if not await _can_view_claim(db, claim, uuid.UUID(user["sub"]), user.get("role", "")):
        raise HTTPException(status_code=403, detail="Not authorized to view this expense claim")

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


@router.delete("/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_travel_application(
    claim_id: uuid.UUID,
    db: SessionDep,
    user: CurrentUserDep,
    token: BearerTokenDep,
):
    """Hard-delete an unapproved Travel Application.

    Restricted to TRA: EXP/MIL/TRV carry budget and payment consequences, so
    this does not open hard delete for them. The record is gone for good —
    approval history included — and the claim number is retired (numbering
    takes max-suffix + 1 and never reuses a gap).
    """
    from sqlalchemy import func, select as sa_select
    from app.models.expense import ExpenseClaim as EC

    claim = await expense_crud.get_by_id(db, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Expense claim not found")

    user_id = uuid.UUID(user["sub"])
    role = user.get("role", "")

    # Type and status are checked before ownership on purpose: the owner of an
    # approved TRA should be told it is too late, not that they lack rights.
    if claim.claim_type != "TRA":
        raise HTTPException(
            status_code=409,
            detail=f"Only Travel Applications can be deleted, not {claim.claim_type}",
        )
    if claim.status not in _DELETABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete a Travel Application in status '{claim.status}'",
        )
    if not can_delete_claim(claim, user_id, role):
        raise HTTPException(
            status_code=403,
            detail="Only the applicant can delete this Travel Application",
        )

    # travel_application_id is ON DELETE SET NULL, so a referencing TRV would
    # silently lose its authorization basis. Unreachable today (the TRV gate
    # requires an APPROVED TRA, and approved is not deletable) — one query to
    # keep it that way if the two rules ever drift.
    referencing = (await db.execute(
        sa_select(func.count()).select_from(EC)
        .where(EC.travel_application_id == claim_id)
    )).scalar_one()
    if referencing:
        raise HTTPException(
            status_code=409,
            detail="This Travel Application is referenced by a travel expense claim",
        )

    # Drop attachment blobs before the rows cascade away, otherwise file-api
    # accumulates orphans. Best-effort: the claim going away matters more.
    for att in claim.attachments:
        if att.file_id:
            try:
                await delete_from_file_server(uuid.UUID(att.file_id), token)
            except Exception:
                logger.warning("file-api delete failed for %s; continuing", att.file_id)

    await expense_crud.delete_claim(db, claim)
    await db.commit()


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
    user_id = uuid.UUID(user["sub"])

    # Only the claimant may submit their own claim. approval-api's submit branch
    # validates the STATUS and nothing else, and it cannot take a blanket
    # created_by gate of its own: an NC-imported PO's created_by is the nc-sync
    # service account, so the same rule there would lock a real person out of
    # the sign-off flow. The owner test is unambiguous per-service, so it lives
    # here. Without it, anyone who knew a claim id could push someone else's
    # half-finished draft into approval — after which the owner cannot edit it
    # (only draft/returned are editable) and has to get an approver to send it
    # back. `employee_id` is OA's owner of record; created_by is checked too so
    # an on-behalf-of draft stays submittable by whoever raised it.
    if action == "submit" and user.get("role") != "system_admin":
        if user_id not in (claim.employee_id, claim.created_by):
            raise HTTPException(
                status_code=403,
                detail="Only the claimant can submit this expense claim",
            )

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

    # An approved TRA has total_amount 0 and must never enter the payment path.
    if claim.claim_type == "TRA":
        raise HTTPException(status_code=409, detail="Travel Applications are not payable")

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
