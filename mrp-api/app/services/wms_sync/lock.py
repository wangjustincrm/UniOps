"""One WMS sync at a time, across every process that might start one.

Two things can fire a sync: the scheduler tick (every process running this
service has one) and the Refresh button an admin presses. `run_wms_sync`
DELETEs the whole mirror and re-inserts it, so two overlapping runs would take
turns emptying each other's snapshot — and the Inventory screen reads that
table while they do it.

A Postgres advisory lock is the right instrument, and it must be the
**transaction-scoped** one (`pg_try_advisory_xact_lock`), not the session
variant.

★ Why, the hard way (2026-08-19): the first version took the session-level
lock and released it with `pg_advisory_unlock` in a `finally`. An advisory
lock belongs to a CONNECTION, but this code holds a SQLAlchemy `Session`, and
a Session does not promise to keep the same connection across the `commit()`
that `run_wms_sync` does in the middle of the locked section. In production
the two landed on different backends:

    PROBE lock:   pid=46080 got=True
    PROBE unlock: pid=46254 released=False   (locked on pid=46080)

`pg_advisory_unlock` on a connection that never held the lock simply returns
false — no error, nothing in the log — and connection 46080 kept the lock for
the rest of its pooled life. `is_sync_running()` then answered "yes" forever,
so the Inventory page showed "Syncing from WMS…" with a spinner that never
stopped, and every later Refresh got a 409.

The transaction-scoped lock removes the failure mode rather than papering over
it: Postgres releases it when the transaction ends — on commit, on rollback,
on the connection dying — so there is no unlock statement that can miss, and a
killed process cannot strand it. The critical section is exactly one
transaction (the snapshot replace), which is what needed protecting.

Cost of the change: the lock is released by `run_wms_sync`'s own commit, i.e.
the moment the new snapshot is durable. A second sync starting immediately
after would redo the work harmlessly — it can no longer interleave with the
delete-and-refill that was the actual hazard.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# "WMS_SYNC" in hex — distinct from booking-api's 0x424F4F4B53434844
# ("BOOKSCHED") and vms-api's 0x564D53434845440. Transaction- and
# session-scoped advisory locks share one lock space, so this still excludes
# anything that takes the same key the session way.
ADVISORY_LOCK_KEY = 0x574D535F53594E43


@asynccontextmanager
async def wms_sync_lock(db: AsyncSession):
    """Yield True if this transaction now holds the sync lock, False if
    somebody else does. Never blocks — a caller that did not get it should give
    up and say so, not queue behind a run doing the same work.

    There is deliberately no release step: the lock ends with the transaction.
    Callers must therefore do the protected work on THIS session, and must not
    expect the lock to outlive their commit.
    """
    got = (await db.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY}
    )).scalar_one()
    yield bool(got)


async def is_sync_running(db: AsyncSession) -> bool:
    """Is somebody holding the sync lock right now?

    Read-only, so the Admin screen can say "a sync is running" without the act
    of asking taking the lock (which `pg_try_advisory_xact_lock` would do). The
    key is stored split across `classid`/`objid` for a two-int lock, so the
    halves are recombined here to compare against ours.

    `pg_locks` lists transaction-scoped advisory locks the same way it lists
    session ones, so this reads the same for both.
    """
    held = (await db.execute(text(
        "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
        "AND ((classid::bigint << 32) | objid::bigint) = :k LIMIT 1"
    ), {"k": ADVISORY_LOCK_KEY})).scalar()
    return held is not None
