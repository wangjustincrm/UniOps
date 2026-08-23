"""缺票逾期扫描 —— 每日把过期未收到票的排期行置 overdue 并提醒协议责任人。

复用 daily_followup 的调度形状(同一个 followup_time),但**独立开关**:
notification_settings.agreement_overdue_enabled,默认 True。

⚠️ 与 daily_followup 一样是**单实例假设**。epms-api 若扩到多副本,扫描会重复
跑。这是既有模式的既有问题,本期沿用,不新增也不解决。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.crud.config import get_or_create as get_config
from app.db import session as session_module
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.task import Task
from app.models.user import User
from app.services.email import send_email
from app.services.notification import _build_email_html, _smtp_kwargs, send_admin_alert
from app.tasks.daily_followup import _load_schedule, _RECHECK_SECONDS, _seconds_until_next_run

logger = logging.getLogger(__name__)

# 催票任务类型。与 confirm_period 一样挂在 document_type="agr" 上,
# notification._task_link 已把 agr 深链到 EPMS 的 /agreements/{id}。
CHASE_TASK_TYPE = "chase_agreement_invoice"


async def sweep_overdue_periods(db) -> list[AgreementPaymentSchedule]:
    """状态扫描。无条件跑 —— 开关只管发不发通知,数据该对还是要对。

    显式限定 schedule_type='period':milestone 行本期没有 expected_date,但
    Phase 1C 若给阶段加了可选日期,靠 NULL 隐式过滤就会静默失效。expected_date
    的 NULL 检查是多余的(period 行永远有值),特意不写,避免它悄悄变回那个
    隐式过滤器。

    Whole-branch review finding: this used to filter ONLY on the schedule row
    itself, with no join back to the agreement — a cancelled (or closed)
    agreement's still-"pending" rows kept flipping to "overdue" and emailing
    the owner every single sweep, for the rest of its validity window (a
    3-year weekly agreement cancelled on day one would nag its owner weekly
    for three years, since the toggle defaults ON). "expired" is deliberately
    KEPT admissible here — its final bill legitimately still arrives after
    valid_to, so that agreement's trailing periods must still be tracked.
    """
    today = date.today()
    rows = (await db.execute(
        select(AgreementPaymentSchedule)
        .join(PurchaseAgreement, PurchaseAgreement.id == AgreementPaymentSchedule.agreement_id)
        .where(
            AgreementPaymentSchedule.schedule_type == "period",
            AgreementPaymentSchedule.status == "pending",
            PurchaseAgreement.status.notin_(("cancelled", "closed")),
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


async def _close_settled_chase_tasks(db) -> None:
    """关掉那些协议已经没有任何 overdue 期次的催票任务。

    任务的存续条件就是「这份协议还有缺票的期次」——期次被认领(claim 后转
    received)、或协议被取消/关闭后 sweep_overdue_periods 不再产出它,任务就该
    自动收口,否则会永远挂在 owner 的收件箱里。这里不看 completed_by:催票任务
    没有「用户驳回」语义,状态完全由单据决定。
    """
    still_overdue = (
        select(AgreementPaymentSchedule.agreement_id)
        .where(AgreementPaymentSchedule.status == "overdue")
        .distinct()
    )
    rows = (await db.execute(select(Task).where(
        Task.type == CHASE_TASK_TYPE,
        Task.document_type == "agr",
        Task.is_completed.is_(False),
        Task.document_id.not_in(still_overdue),
    ))).scalars().all()
    now = datetime.now(timezone.utc)
    for task in rows:
        task.is_completed = True
        task.completed_at = now
    await db.flush()


async def _ensure_chase_task(db, agr: PurchaseAgreement, owner: User, count: int) -> None:
    """给协议 owner 派一条催票任务。每份协议同时只保留一条开放任务。

    邮件本身只在期次**刚翻成 overdue** 的那一趟发出,所以光有邮件时,这件事在
    收件人当天读完邮件之后就再无落点——Task Inbox 里没有任何东西提示还欠着票。
    任务是这条通知的常驻落点,由 _close_settled_chase_tasks 在票到齐后收口。
    """
    existing = (await db.execute(select(Task).where(
        Task.type == CHASE_TASK_TYPE,
        Task.document_type == "agr",
        Task.document_id == agr.id,
        Task.is_completed.is_(False),
    ).limit(1))).scalar_one_or_none()
    if existing is not None:
        return
    db.add(Task(
        type=CHASE_TASK_TYPE,
        priority="normal",
        document_type="agr",
        document_id=agr.id,
        document_number=agr.number,
        assigned_role=owner.role or "dept_manager",
        assigned_user_id=owner.id,
        title=f"Chase missing invoice: {agr.number}",
        description=(
            f"{count} billing period(s) on agreement {agr.number} are past their "
            f"expected invoice date. Please chase the vendor for the invoice(s), "
            f"then match them to this agreement."
        ),
        vendor=agr.vendor_name,
    ))
    await db.flush()


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
        # 任务先于邮件建立,而且不受 SMTP 成败影响:邮件发不出去时,收件箱里的
        # 这条任务是唯一还能让人知道欠票的东西。
        await _ensure_chase_task(db, agr, owner, len(rows))
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
            # 票到齐 / 协议关闭后收口催票任务。与 sweep 一样无条件跑:
            # agreement_overdue_enabled 只管发不发通知,收件箱里的任务状态
            # 属于数据正确性,不能被一个通知开关左右。
            await _close_settled_chase_tasks(db)
            cfg = await get_config(db)
            notify = (cfg.notification_settings or {}).get("agreement_overdue_enabled", True)
            # Whole-branch review finding: the status flips are data
            # correctness and must not sit in the same open transaction as N
            # SMTP round-trips below — a slow mail server held row locks on
            # agreement_payment_schedule for the whole sweep, and (session is
            # expire_on_commit=False, so `flipped`/`cfg` stay usable past
            # this point) nothing downstream needs the flips to still be
            # uncommitted. Commit them first, THEN notify.
            await db.commit()
            if flipped and notify:
                await _notify_owners(db, cfg, flipped)
                # _notify_owners 建的催票任务落在上面那次 commit 之后,不再提交
                # 一次就会随 session 关闭被丢掉(邮件已经发出去了,收件箱却空)。
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
