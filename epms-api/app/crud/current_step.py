"""Enrich list items with their current approval step, sourced from the open
approve_{doctype} task (the resync-authoritative 'real current step') rather
than approval_step_idx, which over-budget injection and stale routing can skew.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    "gm_or_opm": 40,
    "finance_bp": 50,
    "finance_manager": 60,
    "vendor_manager": 70,
}


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role) or role.replace("_", " ").title()


async def enrich_current_step(db: AsyncSession, doc_type: str, items: list) -> None:
    """Set `.current_step` on each item: a dict for in_review items with an open
    approval task, else None. Mutates items in place. One batched task query +
    one batched user-name query (no N+1)."""
    for it in items:
        it.current_step = None

    in_review = [it for it in items if getattr(it, "status", None) == "in_review"]
    if not in_review:
        return

    ids = [it.id for it in in_review]
    task_type = f"approve_{doc_type}"
    rows = (await db.execute(
        select(Task).where(
            Task.document_type == doc_type,
            Task.type == task_type,
            Task.is_completed == False,  # noqa: E712
            Task.document_id.in_(ids),
        ).order_by(Task.created_at.asc())
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

    for it in in_review:
        t = task_by_doc.get(it.id)
        if t is None:
            continue
        it.current_step = {
            "role": t.assigned_role,
            "label": role_label(t.assigned_role),
            "approver_name": name_by_user.get(t.assigned_user_id) if t.assigned_user_id else None,
            "since": t.created_at,
        }
