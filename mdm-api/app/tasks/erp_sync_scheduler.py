"""按间隔自动跑 ERP 主数据同步(物料/供应商/人员)。

形状照抄 epms-api/app/tasks/nc_purchase_sync_scheduler.py。差异只有两处:
* 一次调度顺序跑完三个小类 —— 三张表都很小(合计不到 4000 行),
  没必要给它们三个独立的排期。
* 单个小类失败不让另外两个连坐:供应商和人员的数据与物料无关,
  一类的接口抖动没有理由把整轮同步都废掉。

默认一天一次:主数据变动很少,而且每次都是对外部接口的一串 HTTP 调用。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.config import settings
from app.db.base import AsyncSessionLocal
from app.models.company_config import CompanyConfig
from app.models.erp_sync_state import ErpSyncState
from app.services.erp_sync import sync_kind

logger = logging.getLogger(__name__)

# 一天一次。主数据变动很少,而且这是对外部接口的调用,不是本地读。
DEFAULT_INTERVAL_MINUTES = 1440
MAX_INTERVAL_MINUTES = 1440
TICK_SECONDS = 60

# 固定顺序:三者互不依赖,但固定顺序让日志可预期。
SUB_KINDS = ("material", "supplier", "person")


def resolve_interval_minutes(raw: object) -> int:
    """把库里存的值强制成一个能用的分钟数。NULL→默认,超界→钳制。"""
    if raw is None:
        return DEFAULT_INTERVAL_MINUTES
    if isinstance(raw, bool) or not isinstance(raw, int):
        logger.warning("ERP MDM sync interval is %r, not an integer — using %d minutes",
                       raw, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    if raw < 0:
        return 0
    return min(raw, MAX_INTERVAL_MINUTES)


def is_due(*, last_started_at: datetime | None, interval_minutes: int,
           now: datetime) -> bool:
    """纯决策。从上次同步的开始时间量;未来时间(时钟回拨)算未到点。"""
    if interval_minutes <= 0:
        return False
    if last_started_at is None:
        return True
    if last_started_at.tzinfo is None:
        last_started_at = last_started_at.replace(tzinfo=timezone.utc)
    return now - last_started_at >= timedelta(minutes=interval_minutes)


async def load_interval_minutes() -> int:
    """读配置。读库炸了降级成默认值,不杀循环。"""
    try:
        async with AsyncSessionLocal() as db:
            raw = (await db.execute(
                select(CompanyConfig.erp_mdm_sync_interval_minutes).limit(1)
            )).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        logger.error("ERP MDM scheduler: failed to read the interval (%s) — using %d minutes",
                     exc, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    return resolve_interval_minutes(raw)


async def last_sync_started_at() -> datetime | None:
    """三个小类里最早的那次 last_synced_at —— 只要有一类还没轮到,整轮就算没跑完。

    某一类从来没同步过时该行不存在,min() 得到 NULL,is_due 因此返回 True。
    """
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(func.count(ErpSyncState.kind), func.min(ErpSyncState.last_synced_at))
            .where(ErpSyncState.kind.in_(SUB_KINDS))
        )).one()
    count, earliest = rows
    if count < len(SUB_KINDS):
        return None      # 有小类从没跑过 —— 该跑
    return earliest


async def run_all_kinds(db) -> None:
    """顺序跑三小类的增量同步。单类异常只记日志,不中断其余。"""
    for kind in SUB_KINDS:
        try:
            result = await sync_kind(db, kind, full=False)
            logger.info("ERP MDM scheduler: %s ok — %s", kind, result)
        except Exception as exc:  # noqa: BLE001 — 一类失败不连坐
            logger.error("ERP MDM scheduler: %s failed: %s", kind, exc)


async def run_all_kinds_session() -> None:
    """run_all_kinds 的开 session 包装。

    单独一层是为了可测:run_tick 的测试 patch 掉这一个函数,就完全不碰数据库;
    run_all_kinds 自己的测试则直接传一个假 db 进去。两者各测各的。
    """
    async with AsyncSessionLocal() as db:
        await run_all_kinds(db)
        await db.commit()


async def run_tick() -> str:
    """一次调度决策。'disabled' | 'not_due' | 'synced' | 'failed'。"""
    interval = await load_interval_minutes()
    if interval <= 0:
        return "disabled"
    if not is_due(last_started_at=await last_sync_started_at(),
                  interval_minutes=interval, now=datetime.now(timezone.utc)):
        return "not_due"
    try:
        await run_all_kinds_session()
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled ERP MDM sync failed")
        return "failed"
    return "synced"


async def erp_sync_loop() -> None:
    """无限循环;以 asyncio.create_task(erp_sync_loop()) 启动。"""
    if not settings.nc_sync_scheduler_enabled:
        logger.info("ERP MDM sync scheduler disabled (nc_sync_scheduler_enabled=false)")
        return
    logger.info("ERP MDM sync scheduler started (tick=%ss)", TICK_SECONDS)
    while True:
        try:
            await run_tick()
        except asyncio.CancelledError:
            logger.info("ERP MDM sync scheduler stopping")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("ERP MDM sync tick failed")
        await asyncio.sleep(TICK_SECONDS)
