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


# ── Who is an approver here ─────────────────────────────────────────────────
#
# The approval half of this list reads the shared `tasks` table (written by
# approval-api) — the SAME source `_can_act_on_claim` gates the Approve button
# on, and the same source `expenses.py::my_actions` (the Portal inbox) was
# moved to. It used to select every document parked at a workflow step whose
# role the caller's JWT carried, which meant:
#
#   * no department scope — `dept_manager` is a populous role, so every
#     department manager saw every company claim at that step, requester name
#     and amount included. approval-api PINS a dept_manager task to the one
#     manager who routes for that document's department, so reading tasks
#     restores the scope without re-deriving any routing rules here;
#   * no role union — a user whose finance_bp is an ADDITIONAL role (they are
#     assignments, not JWT claims) saw none of the documents they had to act on;
#   * no delegation — a stand-in's inbox was empty for the documents they were
#     covering;
#   * and rows the caller could see but not act on, because the detail page's
#     Approve button asks `_can_act_on_claim`, i.e. the tasks table.
#
# Same deliberate trade-off my_actions made: a document whose approval task was
# closed while the document stayed open (the Mark Done incident) no longer
# appears. Nobody can approve it — it needs the heal script, not an inbox row.
# system_admin gets no bypass here either, for the same reason: the inbox is
# "what I must act on", and the admin's whole-company view is the list page.
#
# The payment half is a role pool with no per-document task, so it stays
# role-based — but off `expenses.py::_CAN_PAY`, imported rather than copied.
# The copy that used to live here had drifted: it was missing `payment_officer`,
# the role that actually executes payments now, so the person responsible for
# paying saw nothing to pay.


def _exp_task_type(status: str, is_own: bool, is_approvable: bool, can_pay: bool,
                   claim_type: str) -> str | None:
    if status == "returned" and is_own:
        return "revise_expense"
    # A Travel Application carries no money and never enters the payment path;
    # its total is 0.00, so a "Record Payment" card for one is pure noise.
    if status == "approved" and can_pay and claim_type.upper() != "TRA":
        return "pay_expense"
    if status in ("submitted", "in_review"):
        if is_own:
            return "submitted_expense"
        return "approve_expense" if is_approvable else None
    return None


def _pa_task_type(status: str, is_own: bool, is_approvable: bool, can_pay: bool) -> str | None:
    if status == "returned" and is_own:
        return "revise_pa"
    if status == "approved" and can_pay:
        return "pay_pa"
    if status in ("submitted", "in_review"):
        if is_own:
            return "submitted_pa"
        return "approve_pa" if is_approvable else None
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
    from app.api.v1.expenses import (
        _CAN_PAY, _user_role_codes, approvable_document_ids,
    )
    from app.models.expense import ExpenseClaim as EC

    role = user.get("role", "")
    user_id = uuid.UUID(user["sub"])
    roles = await _user_role_codes(db, user_id, role)      # primary ∪ additional
    can_pay = any(r in _CAN_PAY for r in roles)

    # One definition of "is this mine to approve", shared with my_actions and
    # the list endpoints — see approvable_document_ids. The inline copy that
    # used to live here is what drifted: no department scope, no role union, no
    # delegation, long after my_actions had all three.
    approvable_ids = await approvable_document_ids(db, user_id, role)

    tasks: list[OaTaskItem] = []

    # ── Expense claims ────────────────────────────────────────────────────────

    exp_conditions = [
        (EC.employee_id == user_id) & (EC.status.in_(["submitted", "in_review", "returned"])),
    ]
    if approvable_ids:
        exp_conditions.append(
            EC.id.in_(approvable_ids) & EC.status.in_(["submitted", "in_review"])
        )
    if can_pay:
        # TRA excluded: an approved Travel Application has total_amount 0 and
        # never enters the payment path (same rule as my_actions / list_expenses).
        exp_conditions.append((EC.status == "approved") & (EC.claim_type != "TRA"))

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
        tt = _exp_task_type(claim.status, is_own, claim.id in approvable_ids,
                            can_pay, claim.claim_type)
        if tt is None:
            continue
        seen_exp.add(claim.id)

        # TRA was falling through `.get(ct, "cfm")` into the Custom Form bucket:
        # a Travel Application showed up labelled "Custom Form" and deep-linked
        # to /expenses/{id} instead of /travel/{id}.
        ct = claim.claim_type.upper()
        doc_type = {"EXP": "exp", "MIL": "mil", "TRV": "trv", "TRA": "tra"}.get(ct, "cfm")

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
    #
    # Deliberately absent. OA's own PA — the Direct PA — is retired (see
    # DIRECT_PA_RETIRED in api/v1/pa.py), so there is no OA payment application
    # left for this inbox to be about.
    #
    # EPMS's PAs used to appear here too, deep-linking into OA's /pa/:id. They
    # are EPMS documents: epms-api's own task list covers approve_pa (its
    # liveness map names both `pa` and `pa_dir`), and the Portal home inbox
    # merges that feed, so nothing is lost by leaving them out of OA's. What
    # WOULD be lost by keeping them is the link: OA no longer has a /pa route
    # for the card to open.

    tasks.sort(key=lambda t: t.created_at, reverse=True)
    return OaTaskListResponse(items=tasks, total=len(tasks))
