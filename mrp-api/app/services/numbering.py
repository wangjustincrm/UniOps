"""Shared "human-readable document number" generator for tables whose
number column carries a `unique=True` constraint (`ForecastVersion.version_no`,
`MrpMpsRun.run_no`).

Both numbers are `{PREFIX}-{YYYYMM}-{MMDDHH}` -- `YYYYMM` is the caller's
anchor/horizon month baked into `prefix` (no dash, e.g. `202610`);
`MMDDHH` is the creation month/day/hour, replacing what used to be random
hex/a daily counter, at the business owner's request (outlook filenames
should say *when* a snapshot was taken, not carry an opaque suffix). This
is round two of that request: the previous shape was `DDHHMM` (day/hour/
*minute*, no creation month), which the owner flagged as ambiguous --
without a month of its own, a `DDHHMM` number could be misread as a date
inside the horizon month. `MMDDHH` fixes that by putting creation month
where the horizon month can't be confused with it, at the cost of the
minute: `MMDDHH` alone is not unique to the minute, or even close --
two creates for the same prefix inside the same UTC **hour** collide on
the column's unique constraint. That is no longer an edge case: an
ordinary working session generating several runs inside one hour hits it
routinely (observed: seven runs in about 35 minutes, all same-hour), not
just near a boundary.

The fix follows this codebase's existing convention for exactly this
problem (see project memory
`project_uniops_document_number_collision` -- an 8-call-site incident fixed
with "max existing tail + 1" serialized by a Postgres advisory lock; and
`app/api/v1/mps.py`'s pre-existing `_next_run_no`, which used that pattern
for the daily `MPS-YYYYMMDD-####` counter it replaced): take an advisory
xact lock scoped to the caller's `(lock_key, prefix)` pair (so two
concurrent requests for the SAME base can't both read "nothing taken yet"
and both try to insert the bare `MMDDHH`), then if the bare timestamp is
already taken, disambiguate with `-2`, `-3`, ... by scanning existing
numbers for the same base and taking max-tail + 1. The common case (no
collision) still gets the exact `MMDDHH` the request asked for; a suffix
now shows up for any second-or-later create in the same creation hour --
routine within a busy session, not a rare tie-break, so callers/UI must
not treat a `-N` number as unusual.

The lock key is `(lock_key, hashtext(prefix))` -- Postgres'
two-integer `pg_advisory_xact_lock(int, int)` overload, not the
single-bigint one -- so only requests that could actually collide (same
table, same prefix, i.e. same anchor/horizon month) ever serialize against
each other. `lock_key` alone identifies the table/column (kept distinct
per caller: `demand_series.py`'s `_VERSION_NO_LOCK_KEY`, `mps.py`'s
`_RUN_NO_LOCK_KEY`); freezing `FCV-202609` and `FCV-202610`
concurrently, or generating MPS runs for two different horizon months at
once, must not wait on each other -- different prefixes produce different
`base` strings and can never collide on the unique constraint, so there is
nothing for a shared lock to protect there. A single constant key shared
by every prefix would over-serialize: unrelated freezes/runs would queue up
behind each other for no reason.

Why not keep the minute too (i.e. `MMDDHHMM`)? Because the owner's request
was specifically to trade the minute for a visible creation month, not to
add both -- and the max-tail+1 scan below is the actual correctness
mechanism regardless of how wide the timestamp is: a wider stamp shrinks
how often the fallback fires, it doesn't remove the need for it. Kept to
`MMDDHH` to match exactly what was asked for.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute


async def next_timestamped_no(
    db: AsyncSession,
    *,
    lock_key: int,
    column: InstrumentedAttribute,
    prefix: str,
    now: datetime | None = None,
) -> str:
    """`{prefix}MMDDHH`, or `{prefix}MMDDHH-N` if that exact number is
    already taken (same prefix, same UTC hour). `now` defaults to the
    current UTC instant; overridable only for tests.

    UTC (not a business-local zone) to match every other timestamp already
    written by this service (`ForecastVersion.confirmed_at`,
    `TimestampMixin`'s `created_at`/`updated_at`, the retired
    `_next_run_no`'s `today`) -- there is no local-timezone concept
    anywhere else in mrp-api to be consistent with instead, and introducing
    one just for this column would make it the odd one out.

    The lock is `(lock_key, hashtext(prefix))`, not `lock_key` alone --
    see module docstring for why: it scopes serialization to requests that
    share BOTH the table (`lock_key`) and the exact base they could
    collide on (`prefix`, which bakes in the anchor/horizon month), so
    concurrent callers for different months never wait on each other.
    `now` is read only AFTER the lock is acquired -- a caller that was
    queued behind another gets its own fresh timestamp when it finally
    runs, not a stale one captured before it started waiting.
    """
    await db.execute(select(func.pg_advisory_xact_lock(lock_key, func.hashtext(prefix))))
    now = now or datetime.now(timezone.utc)
    base = f"{prefix}{now:%m%d%H}"

    existing = (await db.execute(
        select(column).where(column.like(f"{base}%"))
    )).scalars().all()
    if base not in existing:
        return base

    max_suffix = 1
    for no in existing:
        tail = no[len(base):]
        if tail.startswith("-"):
            try:
                max_suffix = max(max_suffix, int(tail[1:]))
            except ValueError:
                continue  # not one of ours (shouldn't happen given the LIKE filter) -- ignore
    return f"{base}-{max_suffix + 1}"
