"""One WMS sync at a time, across every process that might start one.

Two things can fire a sync: the scheduler tick (every process running this
service has one) and the Refresh button an admin presses. `run_wms_sync`
DELETEs the whole mirror and re-inserts it, so two overlapping runs would take
turns emptying each other's snapshot — and the Inventory screen reads that
table while they do it.

A Postgres SESSION-level advisory lock is the right instrument: it is held by
the connection rather than the transaction (so it survives the commits
`run_wms_sync` does internally), it is released automatically if the process
dies, and it costs one round trip. Same idiom as booking-api's and vms-api's
schedulers, with a key of our own.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# "WMS_SYNC" in hex — distinct from booking-api's 0x424F4F4B53434844
# ("BOOKSCHED") and vms-api's 0x564D53434845440.
ADVISORY_LOCK_KEY = 0x574D535F53594E43


@asynccontextmanager
async def wms_sync_lock(db: AsyncSession):
    """Yield True if this session now holds the sync lock, False if somebody
    else does. Never blocks — a caller that did not get it should give up and
    say so, not queue behind a run that is already doing the same work.

    The unlock runs in a `finally` and is deliberately tolerant: if the
    connection was already dropped (which also releases the lock), failing to
    unlock must not turn into the error the caller reports.
    """
    got = (await db.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}
    )).scalar_one()
    try:
        yield bool(got)
    finally:
        if got:
            try:
                await db.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
                await db.commit()
            except Exception:  # noqa: BLE001
                log.warning("WMS sync advisory unlock failed", exc_info=True)


async def is_sync_running(db: AsyncSession) -> bool:
    """Is somebody holding the sync lock right now?

    Read-only, so the Admin screen can say "a sync is running" without the act
    of asking taking the lock (which `pg_try_advisory_lock` would do). The key
    is stored split across `classid`/`objid` for a two-int lock, so the halves
    are recombined here to compare against ours.
    """
    held = (await db.execute(text(
        "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
        "AND ((classid::bigint << 32) | objid::bigint) = :k LIMIT 1"
    ), {"k": ADVISORY_LOCK_KEY})).scalar()
    return held is not None
