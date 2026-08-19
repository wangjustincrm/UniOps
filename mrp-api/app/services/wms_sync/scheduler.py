"""Keep the WMS mirror fresh without anybody remembering to press anything.

Until this existed, `wms_inventory_lots` only moved when someone called
`POST /admin/wms-sync` by hand — and on the day the Inventory feature shipped,
nobody did: the new `uom` column and the whole `wms_lot_locations` table sat
empty in production while the screen showed "No location recorded".

Shape copied from booking-api/app/services/scheduler.py (asyncio task started
in lifespan, Postgres advisory lock, cancelled on shutdown). The one departure:
the interval is **not** a settings/env value. It is the planning parameter
`wms_sync_interval_minutes`, read fresh on every tick, so an admin changing it
in Portal -> Admin -> WMS Sync takes effect within one tick instead of within
one redeploy. 0 means "do not sync automatically".

Why a fixed short tick plus a due-check, rather than sleeping for the interval:
sleeping for the interval would mean a change from 60 minutes to 5 does not
apply until the current hour-long sleep ends, and a restart would reset the
phase of every schedule. Ticking every `WMS_SYNC_TICK_SECONDS` and asking "is
it due?" against the state row makes the schedule a property of the data, not
of this process's uptime — restarts, redeploys and interval changes all behave
the way an operator expects.

Due-ness is measured from `last_synced_at` (the last ATTEMPT), not
`last_success_at`: if WMS is down, retrying once per interval is right, and
retrying every tick is not.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.api.v1.params import (
    DEFAULT_WMS_SYNC_INTERVAL_MINUTES,
    WMS_SYNC_INTERVAL_KEY,
    get_param,
)
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.sync_state import MrpSyncState
from app.services.wms_sync.lock import wms_sync_lock
from app.services.wms_sync.service import run_wms_sync, wms_configured

log = logging.getLogger(__name__)

_SOURCE = "wms"


def resolve_interval_minutes(raw: object) -> int:
    """Coerce whatever is in the parameter row to a usable number of minutes.

    A row written by anything other than the validated PUT endpoint (a manual
    UPDATE, a restored backup from an older schema) could hold a string, a
    float or a negative. None of those should stop the scheduler dead or, worse,
    be read as "every 0.5 minutes" — fall back to the default and say so once
    per tick in the log.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        log.warning("%s is %r, not a number — using the default of %d minute(s)",
                    WMS_SYNC_INTERVAL_KEY, raw, DEFAULT_WMS_SYNC_INTERVAL_MINUTES)
        return DEFAULT_WMS_SYNC_INTERVAL_MINUTES
    minutes = int(raw)
    if minutes < 0:
        log.warning("%s is %r — treating it as 0 (automatic sync off)",
                    WMS_SYNC_INTERVAL_KEY, raw)
        return 0
    return minutes


def is_due(*, last_attempt_at: datetime | None, interval_minutes: int,
           now: datetime) -> bool:
    """Pure decision, so the schedule can be tested without a WMS or a clock.

    A never-synced mirror (`last_attempt_at is None`) is due immediately —
    that is the case this whole module exists for. A `last_attempt_at` in the
    future (clock skew between the app and the database) counts as not due:
    waiting one extra interval is harmless, whereas treating it as overdue
    would sync on every single tick until the clocks agree.
    """
    if interval_minutes <= 0:
        return False
    if last_attempt_at is None:
        return True
    if last_attempt_at.tzinfo is None:
        last_attempt_at = last_attempt_at.replace(tzinfo=timezone.utc)
    return now - last_attempt_at >= timedelta(minutes=interval_minutes)


async def run_tick_with_session(db) -> str:
    """One scheduling decision, on a caller-supplied session.

    Returns why it did what it did — 'disabled' | 'not_configured' |
    'not_due' | 'synced' | 'failed'. Tests call this directly (no asyncio
    loop, no advisory lock); the string is what they assert on, and what the
    log line says in production.
    """
    interval = resolve_interval_minutes(
        await get_param(db, WMS_SYNC_INTERVAL_KEY, DEFAULT_WMS_SYNC_INTERVAL_MINUTES))
    if interval <= 0:
        return "disabled"
    if not wms_configured():
        # Not an error: docker-compose.prod.yml ships WMS_* blank on purpose
        # ("missing values = feature hidden"). Nothing to do, quietly.
        return "not_configured"

    state = await db.get(MrpSyncState, _SOURCE)
    if not is_due(last_attempt_at=state.last_synced_at if state else None,
                  interval_minutes=interval, now=datetime.now(timezone.utc)):
        return "not_due"

    try:
        result = await run_wms_sync(db)
    except Exception:  # noqa: BLE001
        # run_wms_sync already rolled back and recorded last_error on the state
        # row, which is what the Admin screen shows. Swallow it here so one bad
        # WMS response cannot end the scheduler for the life of the process.
        log.exception("Scheduled WMS sync failed")
        return "failed"
    log.info("Scheduled WMS sync: %s", result)
    return "synced"


async def _run_tick() -> None:
    """One tick with its own session and the single-flight lock."""
    async with AsyncSessionLocal() as db:
        async with wms_sync_lock(db) as got:
            if not got:
                log.debug("WMS sync tick skipped — a sync is already running")
                return
            try:
                await run_tick_with_session(db)
            except Exception:  # noqa: BLE001
                await db.rollback()
                log.exception("WMS sync tick failed")


async def _loop() -> None:
    tick = max(10, settings.WMS_SYNC_TICK_SECONDS)
    log.info("WMS sync scheduler started (tick=%ss, interval from the "
             "%s parameter)", tick, WMS_SYNC_INTERVAL_KEY)
    try:
        while True:
            await _run_tick()
            await asyncio.sleep(tick)
    except asyncio.CancelledError:
        log.info("WMS sync scheduler stopping")
        raise


def start() -> asyncio.Task | None:
    """Create the background task (call from lifespan startup); None when the
    scheduler is switched off for this deployment."""
    if not settings.WMS_SYNC_SCHEDULER_ENABLED:
        log.info("WMS sync scheduler disabled (WMS_SYNC_SCHEDULER_ENABLED=false)")
        return None
    return asyncio.create_task(_loop(), name="wms-sync-scheduler")


async def stop(task: asyncio.Task | None) -> None:
    """Cancel and await the background task (call from lifespan shutdown)."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
