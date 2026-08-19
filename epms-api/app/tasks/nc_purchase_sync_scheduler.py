"""Run the NC purchase sync on a schedule instead of on somebody's memory.

The NC65 mirror (purchase orders + arrivals) had exactly one trigger: the
button in Portal -> Admin -> NC Purchase Sync. So how current UniOps' purchase
data was depended on who last pressed it — and the identical gap in the WMS
mirror is what left production showing an empty locations table for two days
after a release.

Shape follows this service's existing background loops (app/tasks/
daily_followup.py, agreement_overdue.py): a plain asyncio task started in
app/main.py's lifespan, with every failure absorbed so one bad run cannot end
the loop for the life of the process.

Three things are deliberate:

* **Interval from company config, re-read every tick.** An admin changing it in
  Portal takes effect within one tick, not at the next redeploy. NULL means
  nobody has chosen and resolves to DEFAULT_INTERVAL_MINUTES; 0 turns the
  schedule off entirely.
* **Incremental only.** `full` deletes and rebuilds the mirror, and it is
  confirm-gated in the API for that reason. Nothing automatic should ever do
  it.
* **Single flight is already solved.** `service.start_run` refuses to start
  while a run row is live (and sweeps stale ones), so a scheduled run and an
  admin pressing the button cannot overlap — the scheduler just treats
  SyncAlreadyRunning as "fine, something else is doing it".
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db import session as session_module
from app.models.config import CompanyConfig
from app.models.nc_purchase_sync import NcPurchaseSyncRun
from app.services.nc_purchase_sync import reader
from app.services.nc_purchase_sync import service as svc

logger = logging.getLogger(__name__)

# Hourly. NC purchase orders and arrivals are entered by people during a
# working day, so minute-level freshness buys nothing, while a full NC read is
# considerably heavier than the WMS snapshot.
DEFAULT_INTERVAL_MINUTES = 60
# A day. Longer than this is not a schedule anybody is relying on; they want it
# off, which is what 0 says.
MAX_INTERVAL_MINUTES = 1440
# How often the loop wakes to ask "is it due?". Short relative to the interval
# so a changed setting applies promptly, and so the schedule is a property of
# the run history rather than of this process's uptime — a restart does not
# reset anybody's phase.
TICK_SECONDS = 60


def resolve_interval_minutes(raw: object) -> int:
    """Coerce the stored value to a usable number of minutes.

    NULL (nobody has chosen) resolves to the default. Anything outside
    0..MAX — which the API layer rejects, but a hand-edited row would not —
    is clamped rather than obeyed: an interval of 0.5 would mean a full NC read
    every 30 seconds.
    """
    if raw is None:
        return DEFAULT_INTERVAL_MINUTES
    if isinstance(raw, bool) or not isinstance(raw, int):
        logger.warning("NC sync interval is %r, not an integer — using %d minutes",
                       raw, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    if raw < 0:
        return 0
    return min(raw, MAX_INTERVAL_MINUTES)


def is_due(*, last_started_at: datetime | None, interval_minutes: int,
           now: datetime) -> bool:
    """Pure decision, so the schedule is testable without NC or a clock.

    Measured from the last run's START, whatever its outcome: a failing NC
    connection should be retried once per interval, not on every tick. A start
    time in the future (clock skew) counts as not due — waiting one extra
    interval is harmless; treating it as overdue would sync continuously.
    """
    if interval_minutes <= 0:
        return False
    if last_started_at is None:
        return True
    if last_started_at.tzinfo is None:
        last_started_at = last_started_at.replace(tzinfo=timezone.utc)
    return now - last_started_at >= timedelta(minutes=interval_minutes)


async def load_interval_minutes() -> int:
    """Read the configured interval. A broken DB read must not kill the loop —
    it degrades to the default, which is also what an unconfigured row means."""
    try:
        async with session_module.AsyncSessionLocal() as db:
            raw = (await db.execute(
                select(CompanyConfig.nc_purchase_sync_interval_minutes).limit(1)
            )).scalar_one_or_none()
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("NC sync scheduler: failed to read the interval (%s) — using %d minutes",
                     exc, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    return resolve_interval_minutes(raw)


async def last_run_started_at() -> datetime | None:
    async with session_module.AsyncSessionLocal() as db:
        started = (await db.execute(
            select(NcPurchaseSyncRun.started_at)
            .order_by(NcPurchaseSyncRun.started_at.desc()).limit(1)
        )).scalar_one_or_none()
        await db.commit()
    return started


async def run_tick() -> str:
    """One scheduling decision. Returns why it did what it did — 'disabled' |
    'not_configured' | 'not_due' | 'already_running' | 'synced' | 'failed' —
    which is what the tests assert on and what the log line says."""
    interval = await load_interval_minutes()
    if interval <= 0:
        return "disabled"
    if not svc.nc_configured():
        # NC_* blank is a deployment with the feature switched off, not a fault
        # to raise once a minute.
        return "not_configured"
    if not is_due(last_started_at=await last_run_started_at(),
                  interval_minutes=interval, now=datetime.now(timezone.utc)):
        return "not_due"

    try:
        # start_run + the load are blocking psycopg2 work; off the event loop so
        # a slow NC read does not stall the API this process is also serving.
        # started_by is None: nobody pressed anything, and inventing a user id
        # would put a person's name on a machine's run.
        run_id = await asyncio.to_thread(
            svc.start_run, "incremental", None, fetch=reader.fetch_nc, run_worker=True)
    except svc.SyncAlreadyRunning:
        # An admin pressed the button, or another replica got there first.
        return "already_running"
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled NC purchase sync failed")
        return "failed"
    logger.info("Scheduled NC purchase sync finished (run %s)", run_id)
    return "synced"


async def nc_purchase_sync_loop() -> None:
    """Infinite loop; run as asyncio.create_task(nc_purchase_sync_loop())."""
    if not settings.nc_sync_scheduler_enabled:
        logger.info("NC purchase sync scheduler disabled (nc_sync_scheduler_enabled=false)")
        return
    logger.info("NC purchase sync scheduler started (tick=%ss)", TICK_SECONDS)
    while True:
        try:
            await run_tick()
        except asyncio.CancelledError:
            logger.info("NC purchase sync scheduler stopping")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("NC purchase sync tick failed")
        await asyncio.sleep(TICK_SECONDS)
