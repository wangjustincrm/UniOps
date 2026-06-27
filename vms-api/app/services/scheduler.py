"""In-process background scheduler.

A single asyncio task started in the FastAPI lifespan that runs the time-based
jobs (`app.services.scheduled_jobs.run_all`) every
`settings.SCHEDULER_INTERVAL_SECONDS`. No external dependency — the monorepo
has no scheduler infrastructure and vms-api runs single-worker uvicorn.

Multi-worker / multi-container safety: each tick takes a Postgres
**session-level advisory lock** (`pg_try_advisory_lock`) before doing any work
and releases it after. If another worker already holds it, this tick is a
no-op. So even if the service is scaled to N replicas, only one runs the jobs
per tick — no double-emails.

A failing tick is logged and swallowed; the loop keeps running so one bad
tick (e.g. transient DB blip) doesn't kill the scheduler for the process
lifetime.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services import scheduled_jobs

log = logging.getLogger(__name__)

# Arbitrary but stable 64-bit key identifying "the VMS scheduler tick". Any
# other process taking this exact advisory lock would contend; we picked a
# value unlikely to collide with other UniOps advisory-lock users.
_ADVISORY_LOCK_KEY = 0x564D_5343_4845_4400  # "VMSCHED\0"


async def _run_tick() -> None:
    """One scheduler iteration. Guarded by an advisory lock so only one
    replica does the work."""
    async with AsyncSessionLocal() as db:
        got = (await db.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY}
        )).scalar_one()
        if not got:
            log.debug("Scheduler tick skipped — advisory lock held elsewhere")
            return
        try:
            summary = await scheduled_jobs.run_all(db)
            await db.commit()
            if any(summary.values()):
                log.info("Scheduler tick: %s", summary)
        except Exception:
            await db.rollback()
            log.exception("Scheduler tick failed")
        finally:
            await db.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY}
            )
            await db.commit()


async def _loop() -> None:
    interval = max(60, settings.SCHEDULER_INTERVAL_SECONDS)
    log.info("VMS scheduler started (interval=%ss)", interval)
    try:
        while True:
            await _run_tick()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("VMS scheduler stopping")
        raise


def start(app) -> asyncio.Task | None:
    """Create the background task (call from lifespan startup). Returns the
    task so the caller can cancel it on shutdown, or None when disabled."""
    if not settings.SCHEDULER_ENABLED:
        log.info("VMS scheduler disabled (SCHEDULER_ENABLED=false)")
        return None
    return asyncio.create_task(_loop(), name="vms-scheduler")


async def stop(task: asyncio.Task | None) -> None:
    """Cancel + await the background task (call from lifespan shutdown)."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
