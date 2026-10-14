"""Keep the NC mirrors fresh on a schedule.

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


async def run_tick_with_session(db) -> str:
    """'disabled' | 'not_configured' | 'not_due' | 'synced' | 'failed'."""
    interval = await params.get_param(db, params.NC_SYNC_INTERVAL_KEY)
    if interval <= 0:
        return "disabled"
    if not nc_configured():
        return "not_configured"
    state = await db.get(InvSyncState, SOURCE)
    if not is_due(last_attempt_at=state.last_synced_at if state else None,
                  interval_minutes=interval, now=datetime.now(timezone.utc)):
        return "not_due"
    try:
        await run_nc_sync(db)
    except Exception:  # noqa: BLE001 — recorded on the state row; keep the loop alive
        log.exception("Scheduled NC inventory sync failed")
        return "failed"
    return "synced"


async def _run_tick() -> None:
    async with AsyncSessionLocal() as db:
        async with nc_sync_lock(db) as got:
            if not got:
                log.debug("NC inventory sync tick skipped — a sync is already running")
                return
            try:
                await run_tick_with_session(db)
            except Exception:  # noqa: BLE001
                await db.rollback()
                log.exception("NC inventory sync tick failed")


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
