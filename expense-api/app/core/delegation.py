"""Active approval delegations — READ-ONLY sibling copy.

⚠️ SIBLING COPY of approval-api/app/crud/delegation.py (authoritative) and
epms-api/app/core/delegation.py. expense-api never writes this table.

WHY `today` IS A BIND PARAMETER, NOT `CURRENT_DATE`:
the containers set no TZ, so a database-side date is the UTC date. Between
20:00 and midnight in Toronto that is already tomorrow, which would expire a
window four hours early on its last day. Passing the plant-local date in also
lets tests assert the boundaries without touching a clock.
"""
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PLANT_TIMEZONE = "America/Toronto"

_ACTIVE = """
      revoked_at IS NULL
  AND start_date <= :today
  AND end_date   >= :today
  AND EXISTS (SELECT 1 FROM users u
              WHERE u.id = d.delegate_user_id AND u.is_active)
"""

# Mirrors _PERSONAL_APPROVAL_ROLES in epms-api/app/crud/task.py. Defined
# literally rather than imported — expense-api cannot import from epms-api.
_PERSONAL_APPROVAL_ROLES = frozenset({"director", "supervisor"})


def local_today() -> date:
    """Today's calendar date at the plant, not in UTC."""
    return datetime.now(ZoneInfo(PLANT_TIMEZONE)).date()


async def active_delegator_ids(
    db: AsyncSession, delegate_user_id: uuid.UUID, today: date | None = None,
) -> set[uuid.UUID]:
    """People whose approvals `delegate_user_id` may act on today.

    One level only — delegation is not transitive. A delegate may legitimately
    cover several delegators at once (the exclusion constraint is per
    delegator, not per delegate), so this returns a set.
    """
    rows = (await db.execute(text(
        f"SELECT d.delegator_user_id FROM approval_delegations d "
        f"WHERE d.delegate_user_id = :me AND {_ACTIVE}"),
        {"me": str(delegate_user_id), "today": today or local_today()},
    )).scalars().all()
    return set(rows)


async def active_delegate_id(
    db: AsyncSession, delegator_user_id: uuid.UUID, today: date | None = None,
) -> uuid.UUID | None:
    """Who is standing in for `delegator_user_id` today, if anyone.

    At most one row can match: the exclusion constraint forbids overlapping
    live windows for one delegator.
    """
    return (await db.execute(text(
        f"SELECT d.delegate_user_id FROM approval_delegations d "
        f"WHERE d.delegator_user_id = :who AND {_ACTIVE}"),
        {"who": str(delegator_user_id), "today": today or local_today()},
    )).scalar_one_or_none()


async def delegated_broadcast_roles(
    db: AsyncSession, delegator_ids: set[uuid.UUID],
) -> set[str]:
    """Role codes the given delegators hold, for matching ROLE-POOL tasks
    (assigned_user_id IS NULL).

    ⚠️ Never feed this into a document's own visibility-scope role resolution.
    Delegation only widens which TASKS the delegate can act on — unioning a
    delegator's roles into the delegate's own scope would hand them the
    delegator's entire visibility, far beyond approving their tasks.
    """
    if not delegator_ids:
        return set()
    ids = [str(i) for i in delegator_ids]
    # is_active is filtered on BOTH halves of the union: a deactivated
    # delegator is not a role holder anywhere else in the system, so neither
    # their PRIMARY role (users.role) nor their ADDITIONAL roles (user_roles)
    # should leak into a delegate's broadcast-role set — surfacing role-pool
    # tasks to the delegate on behalf of someone no longer treated as holding
    # the role.
    rows = (await db.execute(sa.text(
        "SELECT role FROM users WHERE id = ANY(:ids) AND is_active "
        "UNION "
        "SELECT ur.role_code FROM user_roles ur "
        " JOIN users u ON u.id = ur.user_id "
        " WHERE ur.user_id = ANY(:ids) AND u.is_active"),
        {"ids": ids})).scalars().all()
    return {r for r in rows if r} - _PERSONAL_APPROVAL_ROLES
