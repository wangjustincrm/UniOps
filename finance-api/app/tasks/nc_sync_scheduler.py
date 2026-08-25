"""按间隔自动跑 NC 凭证(JV)同步,而不是靠谁记得去点按钮。

形状照抄 epms-api/app/tasks/nc_purchase_sync_scheduler.py —— 同一套语义,
换成读 nc_jv_sync_interval_minutes、触发 services/nc_sync 的 start_run、
从 nc_sync_runs 取上次开始时间。

四条刻意的设计(与模板一致):
* 间隔每 tick 重读 —— 管理员改完一分钟内生效,不用重启。
* NULL 回落默认;0 关闭整个排期。
* 只跑 incremental。full 是删了重建,API 层专门做了 confirm 门禁,
  没有任何自动的东西应该做它。
* 到点从上次 run 的"开始时间"算 —— NC 连不上时是每个 interval 重试一次,
  而不是每个 tick 都撞一次。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.base import AsyncSessionLocal
from app.models.mirrors import CompanyConfig
from app.models.nc_sync import NcSyncRun
from app.services import nc_sync as svc

logger = logging.getLogger(__name__)

# 每小时。凭证是人在工作日里录的,分钟级新鲜度没有意义。
DEFAULT_INTERVAL_MINUTES = 60
# 一天。比这更长就不是谁在依赖的排期了,那是想关掉 —— 关掉用 0 表达。
MAX_INTERVAL_MINUTES = 1440
# 心跳。远短于 interval,所以改设置一分钟内生效,而且排期是 run 历史的属性、
# 不是这个进程 uptime 的属性 —— 重启不会重置任何人的相位。
TICK_SECONDS = 60


def resolve_interval_minutes(raw: object) -> int:
    """把库里存的值强制成一个能用的分钟数。

    NULL(没人设过)回落默认。超出 0..MAX 的值 —— API 层会拒,但手改的行不会 ——
    是钳制而不是照办:interval=0.5 会变成每 30 秒读一次 NC。
    """
    if raw is None:
        return DEFAULT_INTERVAL_MINUTES
    if isinstance(raw, bool) or not isinstance(raw, int):
        logger.warning("JV sync interval is %r, not an integer — using %d minutes",
                       raw, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    if raw < 0:
        return 0
    return min(raw, MAX_INTERVAL_MINUTES)


def is_due(*, last_started_at: datetime | None, interval_minutes: int,
           now: datetime) -> bool:
    """纯决策,所以排期能脱离 NC 和时钟被测试。

    从上一次的 START 算起,不管它的结果如何:NC 连不上应该是每个 interval
    重试一次,而不是每个 tick。开始时间在未来(时钟回拨)算作未到点 ——
    多等一个 interval 无害,当成逾期会连续同步。
    """
    if interval_minutes <= 0:
        return False
    if last_started_at is None:
        return True
    if last_started_at.tzinfo is None:
        last_started_at = last_started_at.replace(tzinfo=timezone.utc)
    return now - last_started_at >= timedelta(minutes=interval_minutes)


async def load_interval_minutes() -> int:
    """读配置。读库炸了不能杀掉循环 —— 降级成默认值,那也正是未配置的含义。"""
    try:
        async with AsyncSessionLocal() as db:
            raw = (await db.execute(
                select(CompanyConfig.nc_jv_sync_interval_minutes).limit(1)
            )).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        logger.error("JV sync scheduler: failed to read the interval (%s) — using %d minutes",
                     exc, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    return resolve_interval_minutes(raw)


async def last_run_started_at() -> datetime | None:
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(NcSyncRun.started_at)
            .order_by(NcSyncRun.started_at.desc()).limit(1)
        )).scalar_one_or_none()


async def run_tick() -> str:
    """一次调度决策。返回它做了什么以及为什么 —— 'disabled' | 'not_configured'
    | 'not_due' | 'already_running' | 'synced' | 'failed'。测试断言的就是这个,
    日志打印的也是这个。"""
    interval = await load_interval_minutes()
    if interval <= 0:
        return "disabled"
    if not svc.nc_configured():
        # NC_* 留空是"这个部署把功能关了",不是每分钟报一次的故障。
        return "not_configured"
    if not is_due(last_started_at=await last_run_started_at(),
                  interval_minutes=interval, now=datetime.now(timezone.utc)):
        return "not_due"

    try:
        # start_run 和加载都是阻塞的 psycopg2 活;放到线程里,免得一次慢的
        # NC 读把这个进程正在服务的 API 也卡住。
        # started_by=None:没人按任何东西,编一个 user id 会把某个人的名字
        # 记到机器跑的这条 run 上。
        run_id = await asyncio.to_thread(
            svc.start_run, "incremental", None, run_worker=True)
    except svc.SyncAlreadyRunning:
        # 管理员按了按钮,或者另一个副本先到了。
        return "already_running"
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled NC voucher sync failed")
        return "failed"
    logger.info("Scheduled NC voucher sync finished (run %s)", run_id)
    return "synced"


async def nc_sync_loop() -> None:
    """无限循环;以 asyncio.create_task(nc_sync_loop()) 启动。"""
    if not settings.nc_sync_scheduler_enabled:
        logger.info("JV sync scheduler disabled (nc_sync_scheduler_enabled=false)")
        return
    logger.info("JV sync scheduler started (tick=%ss)", TICK_SECONDS)
    while True:
        try:
            await run_tick()
        except asyncio.CancelledError:
            logger.info("JV sync scheduler stopping")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("JV sync tick failed")
        await asyncio.sleep(TICK_SECONDS)
