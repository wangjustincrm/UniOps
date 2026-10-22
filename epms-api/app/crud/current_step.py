"""Enrich list items with their current approval step, sourced from the open
approve_{doctype} task (the resync-authoritative 'real current step') rather
than approval_step_idx, which over-budget injection and stale routing can skew.
"""
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.delegation import active_delegate_ids
from app.models.task import Task
from app.models.user import User

# English labels matching the default workflow node labels.
ROLE_LABELS: dict[str, str] = {
    "supervisor": "Supervisor",
    "ap_clerk": "AP Clerk",
    "dept_manager": "Dept Manager",
    "director": "Director",
    "procurement_manager": "Procurement Manager",
    "gm_or_opm": "GM / OPM",
    "finance_bp": "Finance BP",
    "finance_manager": "Finance Manager",
    "finance_director": "Finance Director",
    "vendor_manager": "Vendor Manager",
    "payment_officer": "Payment Officer",
}

# Chain order for grouping/sorting — independent of per-doc approval_step_idx so
# over-budget step injection cannot distort the grouping.
ROLE_ORDER: dict[str, int] = {
    "supervisor": 10,
    "dept_manager": 20,
    "director": 30,
    "procurement_manager": 35,
    # A gm_or_opm step is assigned as the resolved post (gm or opm), so open
    # tasks carry those codes; the Freight PA chain runs OPM before GM.
    "opm": 39,
    "gm_or_opm": 40,
    "gm": 40,
    "ap_clerk": 45,        # legacy PA review step, before Finance BP
    "finance_bp": 50,
    "finance_manager": 60,
    "finance_director": 65,
    "vendor_manager": 70,
}


# Statuses in which a document is waiting on an approver. `submitted` is the
# first step: the engine sets it on submit and only moves to in_review after
# the first approval, so reading in_review alone showed nothing for a PR still
# waiting on its first approver — the lists left Current Step blank and the
# Assistant read the empty step as "approval complete" (ISS-0047, ISS-0076).
AWAITING_APPROVAL = ("submitted", "in_review")


# A document whose current step is unknown (no open task, or a role not in
# ROLE_ORDER) gets a NULL step, so the caller's nulls_last keeps it after every
# known step in BOTH directions — "no value sorts last" (PRD UI-SORT-003).


def status_step_sort_keys(status_col, id_col, doc_type: str) -> tuple:
    """ORDER BY keys for a list's Status column, in SQL so a paged list sorts
    across all pages: status, then (awaiting approval only) the current step's
    place in the approval chain, then that step's approver name (named before
    the role pool once the caller adds nulls_last).

    The current step is the one enrich_current_step shows: the EARLIEST open
    approve_{doc_type} task of the document — same filter, same order. Outside
    submitted / in_review both step keys are NULL, as the step hint is never
    shown there.
    """
    def _earliest_open_task(col):
        return (
            select(col)
            .select_from(Task)
            .outerjoin(User, User.id == Task.assigned_user_id)
            .where(
                Task.document_type == doc_type,
                Task.type == f"approve_{doc_type}",
                Task.is_completed == False,  # noqa: E712
                Task.document_id == id_col,
            )
            .order_by(Task.created_at.asc(), Task.id.asc())
            .limit(1)
            .correlate_except(Task, User)
            .scalar_subquery()
        )

    awaiting = status_col.in_(AWAITING_APPROVAL)
    step_order = case(
        (awaiting, case(ROLE_ORDER, value=_earliest_open_task(Task.assigned_role),
                         else_=None)),
        else_=None,
    )
    approver = case((awaiting, _earliest_open_task(func.lower(User.full_name))), else_=None)
    return (status_col, step_order, approver)


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role) or role.replace("_", " ").title()


def _approver_label(name: str | None, stand_in: str | None) -> str | None:
    """"Sivers" normally; "Sivers (delegated: Mohammadi)" while she is away, so
    a reader can tell why someone else is expected to act."""
    if name and stand_in:
        return f"{name} (delegated: {stand_in})"
    return name


async def enrich_current_step(db: AsyncSession, doc_type: str, items: list) -> None:
    """Set `.current_step` on each item: a dict for items awaiting approval
    (submitted / in_review) with an open approval task, else None. Mutates
    items in place. One batched task query +
    one batched user-name query (no N+1)."""
    for it in items:
        it.current_step = None

    awaiting = [it for it in items if getattr(it, "status", None) in AWAITING_APPROVAL]
    if not awaiting:
        return

    ids = [it.id for it in awaiting]
    task_type = f"approve_{doc_type}"
    rows = (await db.execute(
        select(Task).where(
            Task.document_type == doc_type,
            Task.type == task_type,
            Task.is_completed == False,  # noqa: E712
            Task.document_id.in_(ids),
        # id breaks created_at ties the same way status_step_sort_keys does,
        # so the step shown and the step the list is sorted by are one task.
        ).order_by(Task.created_at.asc(), Task.id.asc())
    )).scalars().all()

    task_by_doc: dict = {}
    for t in rows:
        task_by_doc.setdefault(t.document_id, t)  # earliest open task wins

    user_ids = {t.assigned_user_id for t in task_by_doc.values() if t.assigned_user_id}
    name_by_user: dict = {}
    if user_ids:
        urows = (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(user_ids))
        )).all()
        name_by_user = {uid: name for uid, name in urows}

    # Who is standing in for each assigned approver today, if anyone. One
    # batched active_delegate_ids query for every unique approver on the page
    # (not one query per approver — see delegation.py's ⚠️ EPMS-ONLY note);
    # their names are then resolved in a single batched query too, not one
    # per stand-in.
    stand_in_id_by_user: dict = await active_delegate_ids(db, user_ids)

    stand_in_names: dict = {}
    if stand_in_id_by_user:
        wanted_ids = set(stand_in_id_by_user.values())
        srows = (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(wanted_ids))
        )).all()
        name_by_stand_in_id = {uid: name for uid, name in srows}
        stand_in_names = {
            uid: name_by_stand_in_id.get(sid) for uid, sid in stand_in_id_by_user.items()
        }

    for it in awaiting:
        t = task_by_doc.get(it.id)
        if t is None:
            continue
        it.current_step = {
            "role": t.assigned_role,
            "label": role_label(t.assigned_role),
            "approver_name": _approver_label(
                name_by_user.get(t.assigned_user_id),
                stand_in_names.get(t.assigned_user_id),
            ) if t.assigned_user_id else None,
            "since": t.created_at,
        }
