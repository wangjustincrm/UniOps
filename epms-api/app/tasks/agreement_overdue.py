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
from app.models.user import User
from app.services.email import send_email
from app.services.notification import _build_email_html, _smtp_kwargs, send_admin_alert
from app.tasks.daily_followup import _load_schedule, _RECHECK_SECONDS, _seconds_until_next_run

logger = logging.getLogger(__name__)


async def sweep_overdue_periods(db) -> list[AgreementPaymentSchedule]:
    """状态扫描。无条件跑 —— 开关只管发不发通知,数据该对还是要对。

    显式限定 schedule_type='period':milestone 行本期没有 expected_date,但
    Phase 1C 若给阶段加了可选日期,靠 NULL 隐式过滤就会静默失效。expected_date
    的 NULL 检查是多余的(period 行永远有值),特意不写,避免它悄悄变回那个
    隐式过滤器。
    """
    today = date.today()
    rows = (await db.execute(
        select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.schedule_type == "period",
            AgreementPaymentSchedule.status == "pending",
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


async def _notify_owners(db, cfg, flipped: list[AgreementPaymentSchedule]) -> None:
    """按协议分组,把每份协议的逾期行汇总成一封信发给该协议的 owner_id。

    没有 owner(未设置 / 已停用 / 无邮箱)的协议退回 send_admin_alert 兜底。
    单个 owner 发送失败只记日志,不连累其它 owner 的信,也不能让整趟 sweep
    的 db.commit() 掉车 —— 状态翻转是数据正确性,不能因为一封邮件发不出去
    就回滚。
    """
    agr_ids = {r.agreement_id for r in flipped}
    agreements = (await db.execute(
        select(PurchaseAgreement).where(PurchaseAgreement.id.in_(agr_ids))
    )).scalars().all()
    by_id = {a.id: a for a in agreements}

    rows_by_agreement: dict = {}
    for row in flipped:
        rows_by_agreement.setdefault(row.agreement_id, []).append(row)

    owner_ids = {
        by_id[aid].owner_id for aid in rows_by_agreement
        if by_id.get(aid) and by_id[aid].owner_id
    }
    owners: dict = {}
    if owner_ids:
        result = await db.execute(select(User).where(User.id.in_(owner_ids)))
        owners = {u.id: u for u in result.scalars().all()}

    unowned: list[AgreementPaymentSchedule] = []
    for aid, rows in rows_by_agreement.items():
        agr = by_id.get(aid)
        owner = owners.get(agr.owner_id) if agr and agr.owner_id else None
        if not (owner and owner.is_active and owner.email):
            unowned.extend(rows)
            continue
        lines = "".join(
            f"<li>{agr.number} — {r.period_label} (expected {r.expected_date})</li>"
            for r in rows
        )
        subject = f"Invoice overdue — {agr.number} ({len(rows)} period(s))"
        html = _build_email_html(f"<p>No invoice has arrived for:</p><ul>{lines}</ul>")
        try:
            await send_email(owner.email, subject, html, **_smtp_kwargs(cfg))
        except Exception as exc:  # noqa: BLE001 — one owner's failure can't sink the run
            logger.warning("Agreement overdue: email to owner %s failed: %s", owner.email, exc)

    if unowned:
        lines = "".join(
            f"<li>{by_id[r.agreement_id].number} — {r.period_label} "
            f"(expected {r.expected_date})</li>" for r in unowned)
        await send_admin_alert(
            f"{len(unowned)} agreement invoice(s) overdue (no owner on file)",
            f"<p>No invoice has arrived for:</p><ul>{lines}</ul>", db)


async def run_agreement_overdue() -> None:
    logger.info("Agreement overdue: starting sweep")
    try:
        async with session_module.AsyncSessionLocal() as db:
            flipped = await sweep_overdue_periods(db)
            cfg = await get_config(db)
            notify = (cfg.notification_settings or {}).get("agreement_overdue_enabled", True)
            if flipped and notify:
                await _notify_owners(db, cfg, flipped)
            await db.commit()
        logger.info("Agreement overdue: %d row(s) flipped", len(flipped))
    except Exception as exc:  # noqa: BLE001 — 一次失败不能杀掉循环
        logger.error("Agreement overdue: sweep failed: %s", exc)


async def agreement_overdue_loop() -> None:
    """镜像 daily_followup_loop 的形状:睡到目标时间就直接跑,睡醒后不再拿
    时钟已经走过去的"现在"去重新算一遍还剩多少秒 —— 那样算出来的必然是"已经
    过了,顺延到明天",从而把刚睡完这一觉等到的这次运行跳过去。
    """
    while True:
        hour, minute = await _load_schedule()
        wait = _seconds_until_next_run(hour, minute)
        if wait > _RECHECK_SECONDS:
            await asyncio.sleep(_RECHECK_SECONDS)
            continue
        logger.info("Agreement overdue: next run in %.0f seconds (at %02d:%02dZ)", wait, hour, minute)
        await asyncio.sleep(wait)
        await run_agreement_overdue()
