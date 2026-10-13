"""按间隔自动跑 NC 存货核算/成本镜像同步(AI 财务分析)。

形状照抄 nc_ap_sync_scheduler.py:间隔每 tick 重读、NULL 回落默认、0 关闭、
从上次 run 的开始时间算到点。区别只有一条:这一域每次都是整段替换
(见 services/nc_cost_sync.py),没有 incremental/full 之分。

间隔存在本模块自己的 fa_config,而不是 epms 的 company_config —— 往那张表
加列要在 tests/conftest.py 手工影子化,否则整套测试 DuplicateColumn。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.base import AsyncSessionLocal
from app.models.nc_cost import FaConfig, NcCostSyncRun
from app.services import nc_cost_sync as svc

logger = logging.getLogger(__name__)

# 4 小时。成本数据主要在月末成本计算时整批变化,平时只有出入库单零星进来;
# 一天几次足够,月末想立刻看就让管理员按按钮。
DEFAULT_INTERVAL_MINUTES = 240
MAX_INTERVAL_MINUTES = 1440
TICK_SECONDS = 60


def resolve_interval_minutes(raw: object) -> int:
    if raw is None:
        return DEFAULT_INTERVAL_MINUTES
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULT_INTERVAL_MINUTES
    if raw < 0:
        return 0
    return min(raw, MAX_INTERVAL_MINUTES)


def is_due(*, last_started_at: datetime | None, interval_minutes: int,
           now: datetime) -> bool:
    if interval_minutes <= 0:
        return False
    if last_started_at is None:
        return True
    if last_started_at.tzinfo is None:
        last_started_at = last_started_at.replace(tzinfo=timezone.utc)
    return now - last_started_at >= timedelta(minutes=interval_minutes)


async def load_interval_minutes() -> int:
    try:
        async with AsyncSessionLocal() as db:
            raw = (await db.execute(
                select(FaConfig.sync_interval_minutes).where(FaConfig.id == 1)
            )).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        logger.error("Cost sync scheduler: failed to read the interval (%s) — using %d minutes",
                     exc, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    return resolve_interval_minutes(raw)


async def last_run_started_at() -> datetime | None:
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(NcCostSyncRun.started_at)
            .order_by(NcCostSyncRun.started_at.desc()).limit(1)
        )).scalar_one_or_none()


async def run_tick() -> str:
    """'disabled' | 'not_configured' | 'not_due' | 'already_running' | 'synced' | 'failed'."""
    interval = await load_interval_minutes()
    if interval <= 0:
        return "disabled"
    if not svc.nc_configured():
        return "not_configured"
    if not is_due(last_started_at=await last_run_started_at(),
                  interval_minutes=interval, now=datetime.now(timezone.utc)):
        return "not_due"
    try:
        await asyncio.to_thread(svc.start_run, None, run_worker=True)
    except svc.SyncAlreadyRunning:
        return "already_running"
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled NC cost sync failed")
        return "failed"
    return "synced"


async def nc_cost_sync_loop() -> None:
    """与凭证/应付同步共用 nc_sync_scheduler_enabled 总开关。"""
    if not settings.nc_sync_scheduler_enabled:
        logger.info("Cost sync scheduler disabled (nc_sync_scheduler_enabled=false)")
        return
    logger.info("Cost sync scheduler started (tick=%ss)", TICK_SECONDS)
    while True:
        try:
            await run_tick()
        except asyncio.CancelledError:
            logger.info("Cost sync scheduler stopping")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Cost sync tick failed")
        await asyncio.sleep(TICK_SECONDS)
