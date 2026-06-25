"""
Daily follow-up scheduler — re-notifies users about open tasks.

Runs at 08:00 server time every day via an asyncio background loop.
Started in app/main.py lifespan.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.task import Task
from app.services.notification import dispatch_task_notification

logger = logging.getLogger(__name__)


async def run_daily_followup() -> None:
    """Query all open tasks and dispatch follow-up notifications."""
    logger.info("Daily follow-up: starting notification run")
    try:
        async with AsyncSessionLocal() as db:
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


async def daily_followup_loop(hour: int = 8, minute: int = 0) -> None:
    """
    Infinite asyncio loop that fires run_daily_followup() once per day at hour:minute UTC.

    Run as: asyncio.create_task(daily_followup_loop())
    """
    while True:
        wait = _seconds_until_next_run(hour, minute)
        logger.info("Daily follow-up: next run in %.0f seconds (at %02d:%02dZ)", wait, hour, minute)
        await asyncio.sleep(wait)
        await run_daily_followup()
