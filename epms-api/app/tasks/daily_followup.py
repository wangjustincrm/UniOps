"""
Daily follow-up scheduler — re-notifies users about open tasks.

Runs once a day at ``notification_settings.followup_time`` (UTC, default
08:00) via an asyncio background loop. Started in app/main.py lifespan.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.crud.config import get_or_create as get_config
from app.db import session as session_module
from app.models.task import Task
from app.services.notification import dispatch_task_notification

logger = logging.getLogger(__name__)


async def run_daily_followup() -> None:
    """Query all open tasks and dispatch follow-up notifications.

    Gated by the admin toggle ``notification_settings.daily_followup_enabled``
    (Portal → Admin → Notification Settings). OFF — including rows created
    before the key existed — skips the run entirely; read per-run so flipping
    the toggle takes effect without a restart.
    """
    logger.info("Daily follow-up: starting notification run")
    try:
        # 惰性属性访问而非 from-import:测试 conftest 会把
        # session_module.AsyncSessionLocal 重绑到测试库。
        async with session_module.AsyncSessionLocal() as db:
            cfg = await get_config(db)
            if not (cfg.notification_settings or {}).get("daily_followup_enabled", False):
                logger.info("Daily follow-up: disabled by admin toggle — skipping run")
                await db.commit()
                return
            result = await db.execute(
                select(Task).where(Task.is_completed.is_(False))
            )
            tasks = list(result.scalars().all())
            logger.info("Daily follow-up: %d open task(s) found", len(tasks))
            for task in tasks:
                await dispatch_task_notification(task, db, is_followup=True)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("Daily follow-up error: %s", exc)


def _seconds_until_next_run(hour: int = 8, minute: int = 0) -> float:
    """Calculate seconds until the next occurrence of HH:MM local time."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        # Already past today's run time — schedule for tomorrow
        from datetime import timedelta
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _parse_followup_time(value) -> tuple[int, int]:
    """Parse a ``"HH:MM"`` config value; anything unparseable → (8, 0)."""
    try:
        hh, mm = str(value).strip().split(":")
        hour, minute = int(hh), int(mm)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, AttributeError):
        pass
    logger.warning("Daily follow-up: invalid followup_time %r — falling back to 08:00Z", value)
    return 8, 0


async def _load_schedule() -> tuple[int, int]:
    """Read notification_settings.followup_time from company config → (hour, minute) UTC."""
    try:
        async with session_module.AsyncSessionLocal() as db:
            cfg = await get_config(db)
            raw = (cfg.notification_settings or {}).get("followup_time")
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — a broken DB must not kill the loop
        logger.error("Daily follow-up: failed to load schedule, using 08:00Z: %s", exc)
        return 8, 0
    return _parse_followup_time(raw) if raw is not None else (8, 0)


# Config poll cap: while far from the target time the loop only naps this long
# before re-reading followup_time, so an admin change applies within ~15 min
# instead of after the previously scheduled (up to 24 h away) run.
_RECHECK_SECONDS = 900


async def daily_followup_loop() -> None:
    """
    Infinite asyncio loop that fires run_daily_followup() once per day at the
    configured followup_time (UTC).

    Run as: asyncio.create_task(daily_followup_loop())
    """
    while True:
        hour, minute = await _load_schedule()
        wait = _seconds_until_next_run(hour, minute)
        if wait > _RECHECK_SECONDS:
            await asyncio.sleep(_RECHECK_SECONDS)
            continue
        logger.info("Daily follow-up: next run in %.0f seconds (at %02d:%02dZ)", wait, hour, minute)
        await asyncio.sleep(wait)
        await run_daily_followup()
