"""Human-readable document numbers: INC-2026-0001, CAPA-2026-0014, FA-2026-0007.

The number columns carry a unique constraint, so two requests that both read
"nothing taken yet" would both try to insert 0001 and one would fail with an
IntegrityError in front of a user who was reporting an injury. This codebase
has already been through that once across eight call sites (project memory
`project_uniops_document_number_collision`), and the fix that came out of it is
the one used here: take a Postgres advisory transaction lock, read the highest
tail actually in use, add one.

The lock is scoped to (table, prefix) via the two-integer
`pg_advisory_xact_lock(int, int)` overload, so incidents and corrective actions
never wait on each other, and neither does one year on another. It is released
when the transaction ends, whether it commits or rolls back.

Sequence numbers restart each calendar year, which is what people expect of a
document number and what the year in the middle is for.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ONTARIO = ZoneInfo("America/Toronto")

# One key per numbered table. Distinct so unrelated inserts do not serialize.
LOCK_INCIDENT = 981_001
LOCK_ACTION = 981_002
LOCK_FIRST_AID = 981_003

_SPECS = {
    "incident": (LOCK_INCIDENT, "INC", "ehs_incidents", "incident_no"),
    "action": (LOCK_ACTION, "CAPA", "ehs_actions", "action_no"),
    "first_aid": (LOCK_FIRST_AID, "FA", "ehs_first_aid_log", "log_no"),
}


async def next_number(db: AsyncSession, kind: str, *, now: datetime | None = None) -> str:
    """Allocate the next number for `kind`. Must run inside a transaction.

    The caller's transaction holds the lock until it ends, so the number is
    reserved for as long as the row is being written and released whether the
    insert succeeds or not.
    """
    try:
        lock_key, prefix, table, column = _SPECS[kind]
    except KeyError:
        raise ValueError(f"unknown numbering kind {kind!r}; known: {sorted(_SPECS)}") from None

    # Year comes from Ontario local time: a report filed at 20:00 on 31
    # December belongs to that year, not to the next one it already is in UTC.
    year = (now or datetime.now(ONTARIO)).astimezone(ONTARIO).year
    base = f"{prefix}-{year}-"

    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k, hashtext(:p))"),
        {"k": lock_key, "p": base},
    )
    highest = (
        await db.execute(
            text(
                f"SELECT MAX(CAST(RIGHT({column}, 4) AS INTEGER)) "  # noqa: S608 - table/column from _SPECS
                f"FROM {table} WHERE {column} LIKE :like"
            ),
            {"like": f"{base}%"},
        )
    ).scalar()
    return f"{base}{(highest or 0) + 1:04d}"
