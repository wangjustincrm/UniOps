"""Keep the NC mirrors and the WMS transaction history fresh on a schedule.

Both sources share the interval setting and the single-flight lock, and each
is due-checked against its OWN state row — a WMS outage does not hold back
NC, and vice versa.

Same shape as mrp-api's WMS scheduler (asyncio task started in lifespan, short
fixed tick + "is it due?" against the state row, transaction-scoped advisory
lock). The interval is the `nc_sync_interval_minutes` setting, read on every
tick, so a change on the Settings page applies within one tick. 0 = off.

Due-ness is measured from `last_synced_at` (the last ATTEMPT): if NC is down,
retry once per interval, not every tick.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import InvSyncState
from app.services import params
from app.services.nc_sync.lock import nc_sync_lock
from app.services.nc_sync.service import SOURCE, nc_configured, run_nc_sync
from app.services.wms_sync import service as wms

log = logging.getLogger(__name__)


def is_due(*, last_attempt_at: datetime | None, interval_minutes: int, now: datetime) -> bool:
    """Pure decision. Never-synced = due now; an attempt time in the future
    (clock skew) = not due, rather than due on every tick."""
    if interval_minutes <= 0:
        return False
    if last_attempt_at is None:
        return True
    if last_attempt_at.tzinfo is None:
        last_attempt_at = last_attempt_at.replace(tzinfo=timezone.utc)
    return now - last_attempt_at >= timedelta(minutes=interval_minutes)


SOURCES = (
    (SOURCE, nc_configured, run_nc_sync),
    (wms.SOURCE, wms.wms_configured, wms.run_wms_sync),
)


async def run_tick_with_session(db) -> dict[str, str]:
    """Per source: 'disabled' | 'not_configured' | 'locked' | 'not_due' | 'synced' | 'failed'."""
    interval = await params.get_param(db, params.NC_SYNC_INTERVAL_KEY)
    out: dict[str, str] = {}
    for source, configured, run in SOURCES:
        if interval <= 0:
            out[source] = "disabled"
            continue
        if not configured():
            out[source] = "not_configured"
            continue
        # The lock is transaction-scoped and every sync commits, so it is
        # taken afresh for EACH source — one lock around the loop would be
        # released by the first source's commit and leave the second unguarded.
        async with nc_sync_lock(db) as got:
            if not got:
                out[source] = "locked"
                continue
        state = await db.get(InvSyncState, source)
        if not is_due(last_attempt_at=state.last_synced_at if state else None,
                      interval_minutes=interval, now=datetime.now(timezone.utc)):
            out[source] = "not_due"
            continue
        try:
            await run(db)
            out[source] = "synced"
        except Exception:  # noqa: BLE001 — recorded on the state row; keep the loop alive
            log.exception("Scheduled %s sync failed", source)
            out[source] = "failed"
    return out


async def _run_tick() -> None:
    async with AsyncSessionLocal() as db:
        try:
            result = await run_tick_with_session(db)
            log.debug("Inventory sync tick: %s", result)
        except Exception:  # noqa: BLE001
            await db.rollback()
            log.exception("Inventory sync tick failed")


async def _loop() -> None:
    tick = max(10, settings.INV_NC_SYNC_TICK_SECONDS)
    log.info("NC inventory sync scheduler started (tick=%ss)", tick)
    try:
        while True:
            await _run_tick()
            await asyncio.sleep(tick)
    except asyncio.CancelledError:
        log.info("NC inventory sync scheduler stopping")
        raise


def start() -> asyncio.Task | None:
    if not settings.INV_NC_SYNC_SCHEDULER_ENABLED:
        log.info("NC inventory sync scheduler disabled (INV_NC_SYNC_SCHEDULER_ENABLED=false)")
        return None
    return asyncio.create_task(_loop(), name="inv-nc-sync-scheduler")


async def stop(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
