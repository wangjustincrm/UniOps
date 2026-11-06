"""Daily Bank of Canada rate fetch (FX rate module, 2026-10-27).

BoC publishes the day's average around 16:30 Eastern. Every tick (30 min) the
loop asks one question — is there a successful scheduled fetch for today,
started after 17:00 Toronto time? If not, fetch the last FETCH_WINDOW_DAYS
(re-fetching a few days is idempotent and heals a day a failed run missed).
An empty table gets the one-off backfill from BACKFILL_FROM instead.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.core.config import settings
from app.db.base import AsyncSessionLocal
from app.models.bank import ExchangeRate, FxFetchRun
from app.services import fx_rates

logger = logging.getLogger(__name__)

TORONTO = ZoneInfo("America/Toronto")
PUBLISH_AFTER = time(17, 0)
FETCH_WINDOW_DAYS = 14
TICK_SECONDS = 1800


def is_due(*, now: datetime, last_ok_started_at: datetime | None) -> bool:
    """Pure decision. Due once per Toronto day, after PUBLISH_AFTER."""
    local = now.astimezone(TORONTO)
    if local.time() < PUBLISH_AFTER:
        return False
    if last_ok_started_at is None:
        return True
    last_local = last_ok_started_at.astimezone(TORONTO)
    return not (last_local.date() == local.date() and last_local.time() >= PUBLISH_AFTER)


async def run_tick(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        last_ok = (await db.execute(
            select(func.max(FxFetchRun.started_at)).where(
                FxFetchRun.trigger == "schedule", FxFetchRun.ok.is_(True))
        )).scalar_one_or_none()
        if not is_due(now=now, last_ok_started_at=last_ok):
            return "not_due"
        has_rows = (await db.execute(select(func.count()).select_from(ExchangeRate)
                                     .where(ExchangeRate.source == fx_rates.SOURCE_BOC))).scalar_one()
        today = now.astimezone(TORONTO).date()
        start = fx_rates.BACKFILL_FROM if not has_rows else today - timedelta(days=FETCH_WINDOW_DAYS)
        run = await fx_rates.run_fetch(db, date_from=start, date_to=today, trigger="schedule")
        await db.commit()
        return "fetched" if run.ok else "failed"


async def fx_rate_loop() -> None:
    if not settings.fx_fetch_scheduler_enabled:
        logger.info("FX rate scheduler disabled (fx_fetch_scheduler_enabled=false)")
        return
    logger.info("FX rate scheduler started (tick=%ss)", TICK_SECONDS)
    while True:
        try:
            result = await run_tick()
            if result != "not_due":
                logger.info("FX rate fetch: %s", result)
        except asyncio.CancelledError:
            logger.info("FX rate scheduler stopping")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("FX rate tick failed")
        await asyncio.sleep(TICK_SECONDS)
