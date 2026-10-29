"""Who is asking: name, department and roles, for the assistant (ISS-0073).

"我是谁？我有哪些角色？" was answered "the system cannot identify you", and then
with roles guessed from the person's open tasks — System Admin, the role they
actually held, was not among them. The planner was never told who was asking:
the token carries an id and a base role, and nothing else reached it.

The roles are the EFFECTIVE set, read through the same function the row scope
and the Task Inbox use (access_scope._effective_role_codes): the primary role,
the additional roles granted in Admin, and Director / Supervisor where the
approval routing makes the person one. Labels are identity's own role names
(role_defs), the ones the Admin screens show. This is only ever the caller's
own record, so it needs no settings permission.
"""
import uuid

import sqlalchemy as sa
from sqlalchemy import select

from app.core.access_scope import _effective_role_codes
from app.crud.current_step import role_label
from app.models.department import Department
from app.models.user import User

# Roles nobody grants: the approval routing makes someone one. Said, so
# "why am I a Director?" has an answer.
_DERIVED = {
    "director": "set as a department's Director in Approval Routing",
    "supervisor": "named as someone's supervisor",
}


async def describe(db, user: dict) -> dict | None:
    """The caller as the assistant may describe them, or None without a user id."""
    sub = user.get("sub")
    if not sub:
        return None
    user_id = uuid.UUID(str(sub))
    primary = user.get("role") or ""
    row = (await db.execute(
        select(User.full_name, User.email, Department.name)
        .select_from(User)
        .outerjoin(Department, Department.id == User.department_id)
        .where(User.id == user_id))).first()
    codes = await _effective_role_codes(db, primary, user_id)
    labels = dict((await db.execute(sa.text(
        "SELECT code, label FROM role_defs WHERE code = ANY(:codes)"),
        {"codes": sorted(codes | {primary})})).all())

    def named(code: str) -> str:
        return labels.get(code) or role_label(code)

    additional = [
        {"label": named(c), **({"how": _DERIVED[c]} if c in _DERIVED else {})}
        for c in sorted(codes - {primary}, key=named)
    ]
    return {
        "name": row.full_name if row else None,
        "email": row.email if row else None,
        "department": row.name if row else None,
        "primary_role": named(primary) if primary else None,
        "additional_roles": additional,
    }


def as_text(me: dict) -> str:
    """The answer to "who am I", written in code: nothing in it is guessed."""
    lines = [f"You are signed in as **{me.get('name') or 'an unnamed user'}**"
             + (f" ({me['email']})." if me.get("email") else ".")]
    lines.append("")
    lines.append(f"- Department: {me.get('department') or 'none set'}")
    lines.append(f"- Primary role: {me.get('primary_role') or 'none'}")
    extra = [r["label"] + (f" ({r['how']})" if r.get("how") else "")
             for r in me.get("additional_roles") or []]
    lines.append(f"- Additional roles: {', '.join(extra) if extra else 'none'}")
    return "\n".join(lines)
