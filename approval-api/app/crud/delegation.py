"""Active approval delegations (代班) — the authoritative copy.

⚠️ SIBLING COPIES: epms-api/app/core/delegation.py and
expense-api/app/core/delegation.py carry the same predicate, because those
services cannot import from this one. If the semantics here change, change
them too — each has its own boundary tests that will go red.

WHY `today` IS A BIND PARAMETER, NOT `CURRENT_DATE`:
the containers set no TZ, so a database-side date is the UTC date. Between
20:00 and midnight in Toronto that is already tomorrow, which would expire a
window four hours early on its last day. Passing the plant-local date in also
lets tests assert the boundaries without touching a clock.
"""
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

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
