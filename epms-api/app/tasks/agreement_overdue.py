"""缺票逾期扫描 —— 每日把过期未收到票的排期行置 overdue 并提醒协议责任人。

复用 daily_followup 的调度形状(同一个 followup_time),但**独立开关**:
notification_settings.agreement_overdue_enabled,默认 True。

⚠️ 与 daily_followup 一样是**单实例假设**。epms-api 若扩到多副本,扫描会重复
跑。这是既有模式的既有问题,本期沿用,不新增也不解决。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.crud.config import get_or_create as get_config
from app.db import session as session_module
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.services.notification import send_admin_alert
from app.tasks.daily_followup import _load_schedule, _seconds_until_next_run

logger = logging.getLogger(__name__)


async def sweep_overdue_periods(db) -> list[AgreementPaymentSchedule]:
    """状态扫描。无条件跑 —— 开关只管发不发通知,数据该对还是要对。

    显式限定 schedule_type='period':milestone 行本期没有 expected_date,但
    Phase 1C 若给阶段加了可选日期,靠 NULL 隐式过滤就会静默失效。
    """
    today = date.today()
    rows = (await db.execute(
        select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.schedule_type == "period",
            AgreementPaymentSchedule.status == "pending",
            AgreementPaymentSchedule.expected_date.is_not(None),
        )
    )).scalars().all()
    flipped = []
    for row in rows:
        grace = row.overdue_after_days if row.overdue_after_days is not None else 7
        if row.expected_date + timedelta(days=grace) < today:
            row.status = "overdue"
            flipped.append(row)
    await db.flush()
    return flipped


async def run_agreement_overdue() -> None:
    logger.info("Agreement overdue: starting sweep")
    try:
        async with session_module.AsyncSessionLocal() as db:
            flipped = await sweep_overdue_periods(db)
            cfg = await get_config(db)
            notify = (cfg.notification_settings or {}).get("agreement_overdue_enabled", True)
            if flipped and notify:
                agr_ids = {r.agreement_id for r in flipped}
                agreements = (await db.execute(
                    select(PurchaseAgreement).where(PurchaseAgreement.id.in_(agr_ids))
                )).scalars().all()
                by_id = {a.id: a for a in agreements}
                lines = "".join(
                    f"<li>{by_id[r.agreement_id].number} — {r.period_label} "
                    f"(expected {r.expected_date})</li>" for r in flipped)
                await send_admin_alert(
                    f"{len(flipped)} agreement invoice(s) overdue",
                    f"<p>No invoice has arrived for:</p><ul>{lines}</ul>", db)
            await db.commit()
        logger.info("Agreement overdue: %d row(s) flipped", len(flipped))
    except Exception as exc:  # noqa: BLE001 — 一次失败不能杀掉循环
        logger.error("Agreement overdue: sweep failed: %s", exc)


async def agreement_overdue_loop() -> None:
    while True:
        hour, minute = await _load_schedule()
        await asyncio.sleep(min(_seconds_until_next_run(hour, minute), 900))
        hour, minute = await _load_schedule()
        if _seconds_until_next_run(hour, minute) > 60:
            continue
        await run_agreement_overdue()
        await asyncio.sleep(90)
