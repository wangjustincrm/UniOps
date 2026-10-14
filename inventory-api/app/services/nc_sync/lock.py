"""One NC inventory sync at a time, across processes.

Same instrument as mrp-api's WMS sync lock, for the same reason: the sync
DELETEs and refills every mirror, so two overlapping runs would empty each
other's snapshot. It must be the TRANSACTION-scoped advisory lock — mrp-api
first used the session variant and stranded it on a pooled connection in
production (see mrp-api/app/services/wms_sync/lock.py for the post-mortem).
The lock ends with the transaction that `run_nc_sync` commits.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# "INV_SYNC" in hex — distinct from mrp-api's WMS_SYNC 0x574D535F53594E43.
ADVISORY_LOCK_KEY = 0x494E565F53594E43


@asynccontextmanager
async def nc_sync_lock(db: AsyncSession):
    """Yield True if this transaction now holds the lock, False otherwise.
    Never blocks."""
    got = (await db.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY}
    )).scalar_one()
    yield bool(got)


async def is_sync_running(db: AsyncSession) -> bool:
    held = (await db.execute(text(
        "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
        "AND ((classid::bigint << 32) | objid::bigint) = :k LIMIT 1"
    ), {"k": ADVISORY_LOCK_KEY})).scalar()
    return held is not None
