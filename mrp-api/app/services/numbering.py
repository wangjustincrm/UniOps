"""Shared "human-readable document number" generator for tables whose
number column carries a `unique=True` constraint (`ForecastVersion.version_no`,
`MrpMpsRun.run_no`).

Both numbers are `{PREFIX}-{month}-{DDHHMM}` -- day-of-month/hour/minute of
creation, replacing what used to be random hex/a daily counter, at the
business owner's request (outlook filenames should say *when* a snapshot was
taken, not carry an opaque suffix). `DDHHMM` alone is not unique: two
creates for the same prefix+month inside the same UTC minute collide on the
column's unique constraint. That is not hypothetical here -- it has already
happened during manual testing and during an automated pass that created
three outlooks a couple of minutes apart.

The fix follows this codebase's existing convention for exactly this
problem (see project memory
`project_uniops_document_number_collision` -- an 8-call-site incident fixed
with "max existing tail + 1" serialized by a Postgres advisory lock; and
`app/api/v1/mps.py`'s pre-existing `_next_run_no`, which used that pattern
for the daily `MPS-YYYYMMDD-####` counter it replaces): take an advisory
xact lock scoped to the caller (so two concurrent requests can't both read
"nothing taken yet" and both try to insert the bare `DDHHMM`), then if the
bare timestamp is already taken, disambiguate with `-2`, `-3`, ... by
scanning existing numbers for the same base and taking max-tail + 1. The
common case (no collision) still gets the exact `DDHHMM` the request asked
for; only an actual same-minute collision pays for a suffix.

Why not just add seconds (DDHHMMSS)? It shrinks the collision window but
doesn't close it -- two requests can still land in the same second (the
observed incidents already did) -- and it still needs the same fallback
logic, so it buys determinism-under-load for nothing: the max-tail+1 scan
below is the actual correctness mechanism either way, seconds or not. Kept
to DDHHMM to match exactly what was asked for.
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
    """`{prefix}DDHHMM`, or `{prefix}DDHHMM-N` if that exact number is
    already taken (same prefix, same UTC minute). `now` defaults to the
    current UTC instant; overridable only for tests.

    UTC (not a business-local zone) to match every other timestamp already
    written by this service (`ForecastVersion.confirmed_at`,
    `TimestampMixin`'s `created_at`/`updated_at`, the retired
    `_next_run_no`'s `today`) -- there is no local-timezone concept
    anywhere else in mrp-api to be consistent with instead, and introducing
    one just for this column would make it the odd one out.
    """
    await db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    now = now or datetime.now(timezone.utc)
    base = f"{prefix}{now:%d%H%M}"

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
