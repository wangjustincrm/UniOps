"""
Service/Project PO 完成日到期扫描 —— 催 PR 的 **Owner** 去建 GR。

PRD §3.13.3 Path B-2 / GR-S-001(a) 一直写着「PO 的预计完成日到达时为 Requester
建任务」,但实现从来只有 Path B-1(纯手工)和发票驱动的 confirm_receipt
(`api/v1/invoices.py::_create_or_renotify_confirm_receipt` —— 要等供应商开票才催,
太晚)。这个模块补上按日期的那条触发路径。

复用而不是新造:命中后签发的仍是 **confirm_receipt** 任务,和发票驱动那条路
同一个类型、同一个受理人口径、同一条深链(`notification._task_link` 对
confirm_receipt 特判成 `/gr/new?poId=`)。这样 Task Inbox 里不会因为触发来源不同
而冒出两种看起来一样的任务,收货后 `crud/gr.py` 现成的关任务逻辑也照样收口。

调度形状镜像 `agreement_overdue`(它又镜像 `daily_followup`):同一个
followup_time、**独立开关**、先 commit 再发信。

受理人是 PR 的 **Owner**(`crud.pr_owner.owner_id_of`:owner_id,空则回落
requester)—— 服务单常由行政代提,真正知道活儿干完没干完的是 Owner。

阶梯是**有序**的:部门经理的升级信只在 Owner 已经真的收到过一封催办信之后
才可能发出,光是天数到了不算。存量单据回填后完成日往往已经过去几个月,少了这
个前置条件,第一轮扫描就会对着几十个从没被催过的人「催一封、同时告状一封」。

⚠️ 与那两个一样是**单实例假设**。epms-api 若扩到多副本,扫描会重复执行。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.config import get_or_create as get_config
from app.crud.pr_owner import owner_id_of
from app.db import session as session_module
from app.models.gr import GoodsReceipt
from app.models.notification_log import NotificationLog
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User
from app.schemas.gr import GR_VOID_STATUSES, SERVICE_TYPES
from app.services.notification import (
    _build_email_html,
    _render,
    _send_with_retry,
    _smtp_kwargs,
    _task_link,
    dispatch_task_notification,
    send_admin_alert,
)
from app.tasks.daily_followup import (
    _RECHECK_SECONDS,
    _load_schedule,
    _seconds_until_next_run,
)

logger = logging.getLogger(__name__)

# PO 状态:服务 GR 从 approved 起就能建(见 api/v1/gr.py 的服务分支),收完货后
# PO 会走到 fully_received/closed,那时不该再催。
OPEN_PO_STATUSES = ("approved", "issued", "partially_received")

REMINDER_TEMPLATE = "service_gr_due"
ESCALATION_TEMPLATE = "service_gr_escalation"

_DEFAULT_REMINDER_DAYS = 1
_DEFAULT_MANAGER_ESCALATION_DAYS = 3


@dataclass
class DueService:
    """一条到期未收货的服务/项目 PO。"""
    po: PurchaseOrder
    pr: PurchaseRequest
    days_over: int          # 今天 - 完成日,>= reminder_days 才会出现在结果里


# ── 扫描(纯查询,不发信不写任务)────────────────────────────────────────────

async def scan_due_service_pos(
    db: AsyncSession,
    *,
    reminder_days: int = _DEFAULT_REMINDER_DAYS,
    today: date | None = None,
) -> list[DueService]:
    """找出「完成日 + reminder_days 已过、却还没有有效 GR」的服务/项目 PO。

    刻意**不**处理没有 pr_id 的 PO:那种 PO 没有 requester 可催(NC/PMS 导入的
    无 PR 单据),群发给整个 requester 角色池是 2026-08-05 那次 59 人事故的成因。
    `notification._dispatch` 里也有同样的兜底,这里是第一道。

    同样刻意跳过 `service_completion_date IS NULL`:该列是本次新增的,上线前的
    存量单据全为空,靠回填脚本补而不是在这里猜一个日期。
    """
    today = today or date.today()
    # 严格小于:完成日 + reminder_days == 今天 仍在阈值内,明天才催。与
    # agreement_overdue 的 `expected_date + grace < today` 同口径,免得两个扫描
    # 对"第 N 天"的理解差一天。
    cutoff = today - timedelta(days=reminder_days)

    # 任何非作废的 GR 都算「已经去建了」—— 建完之后该催的是确认,那是
    # confirm_service_gr 任务的事,不该再由这里催建。
    has_live_gr = (
        select(GoodsReceipt.id)
        .where(
            GoodsReceipt.po_id == PurchaseOrder.id,
            GoodsReceipt.status.not_in(tuple(GR_VOID_STATUSES)),
        )
        .exists()
    )

    rows = (await db.execute(
        select(PurchaseOrder, PurchaseRequest)
        .join(PurchaseRequest, PurchaseRequest.id == PurchaseOrder.pr_id)
        .where(
            PurchaseOrder.type.in_(tuple(SERVICE_TYPES)),
            PurchaseOrder.status.in_(OPEN_PO_STATUSES),
            PurchaseRequest.service_completion_date.is_not(None),
            PurchaseRequest.service_completion_date < cutoff,
            ~has_live_gr,
        )
        .order_by(PurchaseRequest.service_completion_date)
    )).all()

    return [
        DueService(po=po, pr=pr, days_over=(today - pr.service_completion_date).days)
        for po, pr in rows
    ]


# ── 任务签发 ──────────────────────────────────────────────────────────────────

async def _open_confirm_receipt_task(db: AsyncSession, po_id) -> Task | None:
    return (await db.execute(select(Task).where(
        Task.type == "confirm_receipt",
        Task.document_type == "po",
        Task.document_id == po_id,
        Task.is_completed.is_(False),
    ))).scalars().first()


async def _ensure_task(db: AsyncSession, due: DueService) -> tuple[Task, bool]:
    """返回 (任务, 是否本次新建)。已有开放任务就复用,不重复建。"""
    existing = await _open_confirm_receipt_task(db, due.po.id)
    if existing is not None:
        return existing, False

    task = Task(
        type="confirm_receipt",
        priority="normal",
        document_type="po",
        document_id=due.po.id,
        document_number=due.po.number,
        # Routed to the PR's OWNER (crud.pr_owner: owner_id, else the
        # requester). The requester of a service PR is often an admin raising
        # it on someone's behalf; the owner is the person who was there when
        # the work finished and is the only one who can answer this.
        # assigned_role stays "requester" — it is the pool label the Task Inbox
        # and the delegation broadcast read, and the owner acts in the
        # requester's seat on this document; the assignee is what routes.
        assigned_role="requester",
        assigned_user_id=owner_id_of(due.pr),
        title=f"Confirm service completion for {due.po.number}",
        description=(
            f"The expected completion date for {due.po.number} "
            f"({due.pr.service_completion_date}) has passed. "
            f"Please confirm the service was delivered and create a GR."
        ),
        vendor=due.po.vendor_name,
        amount=due.po.total,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task, True


async def _has_been_sent(db: AsyncSession, task_id, template_key: str) -> bool:
    """这条任务有没有成功发出过该模板的信。

    既是升级信的「只发一次」幂等标记,也是「升级前必须先催过」的前置条件 ——
    两者都靠 notification_logs 判定,零迁移。
    """
    return bool((await db.execute(
        select(NotificationLog.id).where(
            NotificationLog.task_id == task_id,
            NotificationLog.template_key == template_key,
            NotificationLog.status == "ok",
        ).limit(1)
    )).scalar_one_or_none())


async def _escalate_to_manager(
    db: AsyncSession, cfg, due: DueService, task: Task,
) -> bool:
    """抄送**被催的那个人**(即 Owner)的部门经理。发过就不再发。

    跟着 Owner 而不是 requester:升级的语义是「我催了他,他没动」,要找的是**他**
    的经理。Owner 常常和 requester 不在一个部门(行政代提的服务单),按 requester
    找就会把状告到一个管不着这件事的经理那里,而真正压得动的人一无所知。
    """
    if await _has_been_sent(db, task.id, ESCALATION_TEMPLATE):
        return False

    from app.crud.pr import _get_dept_manager_id
    from app.services.email import send_email

    owner_id = owner_id_of(due.pr)
    manager_id = await _get_dept_manager_id(db, owner_id, "dept_manager")
    if manager_id is None:
        return False
    manager = await db.get(User, manager_id)
    if manager is None or not manager.is_active or not manager.email:
        return False

    tpl = (cfg.email_templates or {}).get(ESCALATION_TEMPLATE)
    if not tpl:
        return False
    owner = await db.get(User, owner_id)
    owner_label = (owner.full_name or owner.email) if owner else "—"
    variables = {
        "recipient_name": manager.full_name or manager.email,
        # ★ `requester_name` 是生产库里那份 service_gr_escalation 模板已经在用的
        # 占位符(cfg.email_templates 是数据行,不是代码),改名等于让线上模板渲染
        # 出一个空洞。所以键不动,填的是**收到催办的那个人**——正是模板里那句
        # "<b>{requester_name}</b> has not yet confirmed" 想指的人。
        # `owner_name` 是同一个值的新名字,留给以后改模板文案用。
        "requester_name": owner_label,
        "owner_name": owner_label,
        "po_number": due.po.number,
        "days_overdue": due.days_over,
        "completion_date": str(due.pr.service_completion_date),
        "vendor": due.po.vendor_name or "—",
        "company_name": cfg.name,
        "link": _task_link(task.document_type, task.document_id, task_type=task.type),
    }
    subject = _render(tpl["subject"], variables)
    html = _build_email_html(_render(tpl["body"], variables))

    await _send_with_retry(
        "email", task, manager, ESCALATION_TEMPLATE, db,
        send_fn=lambda: send_email(manager.email, subject, html, **_smtp_kwargs(cfg)),
        max_retries=3,
    )
    return True


# ── 一趟扫描 ──────────────────────────────────────────────────────────────────

async def run_service_gr_due() -> None:
    logger.info("Service GR due: starting sweep")
    try:
        async with session_module.AsyncSessionLocal() as db:
            cfg = await get_config(db)
            settings_ = cfg.notification_settings or {}
            if not settings_.get("service_gr_due_enabled", False):
                logger.info("Service GR due: disabled by admin toggle — skipping run")
                await db.commit()
                return

            sla = cfg.service_gr_sla or {}
            reminder_days = int(sla.get("reminder_days", _DEFAULT_REMINDER_DAYS))
            escalation_days = int(
                sla.get("manager_escalation_days", _DEFAULT_MANAGER_ESCALATION_DAYS)
            )
            # 首轮护栏:默认只出清单给 admin 过目,不真发给 requester。管理员在
            # Portal 确认命中集合无误后再关掉这个开关。
            dry_run = settings_.get("service_gr_due_dry_run", True)

            due = await scan_due_service_pos(db, reminder_days=reminder_days)
            logger.info("Service GR due: %d PO(s) past their completion date", len(due))

            if not due:
                await db.commit()
                return

            if dry_run:
                await _report_dry_run(db, due)
                await db.commit()
                return

            # 先把任务落库并提交,再发信 —— 与 agreement_overdue 同一个理由:
            # 任务行是数据正确性,不能和 N 次 SMTP 往返共处一个开着的事务。
            plan: list[tuple[DueService, Task, bool]] = []
            for item in due:
                task, created = await _ensure_task(db, item)
                plan.append((item, task, created))
            await db.commit()

            for item, task, _created in plan:
                # ★ 必须在发本轮提醒**之前**取这个值:dispatch 会就地写一条
                # notification_logs,之后再查就永远是 True,前置条件形同虚设。
                reminded_before = await _has_been_sent(db, task.id, REMINDER_TEMPLATE)

                # 提醒:复用 confirm_receipt 任务,但用到期专用模板,措辞是
                # 「完成日到了」而不是「发票来了」。
                await dispatch_task_notification(
                    task, db,
                    template_key=REMINDER_TEMPLATE,
                    extra_vars={
                        "po_number": item.po.number,
                        "completion_date": str(item.pr.service_completion_date),
                        "days_overdue": item.days_over,
                    },
                )

                # 升级的语义是「催过了还不动」,所以天数到了还不够 —— requester
                # 必须先真的收到过一封催办信。否则回填进来的存量单据(完成日
                # 早就过去几个月)会在第一轮里「一边催他、一边告状给他经理」,
                # 而他一次都没被催过。
                if item.days_over >= escalation_days and reminded_before:
                    await _escalate_to_manager(db, cfg, item, task)
            await db.commit()

        logger.info("Service GR due: sweep finished")
    except Exception as exc:  # noqa: BLE001 — 一次失败不能杀掉循环
        logger.error("Service GR due: sweep failed: %s", exc)


async def _report_dry_run(db: AsyncSession, due: list[DueService]) -> None:
    """dry-run:不建任务、不碰 requester,只把命中清单发给 admin 过目。"""
    lines = "".join(
        f"<li>{d.po.number} — expected {d.pr.service_completion_date} "
        f"({d.days_over} day(s) ago), PR {d.pr.number}</li>"
        for d in due
    )
    await send_admin_alert(
        f"[Dry run] {len(due)} service PO(s) past their completion date",
        "<p>The service GR due-date sweep is in <b>dry-run</b> mode, so no "
        "requester was notified and no task was created. These POs would have "
        f"been nudged:</p><ul>{lines}</ul>"
        "<p>Turn off <b>service_gr_due_dry_run</b> in Portal → Admin → "
        "Notification Settings once this list looks right.</p>",
        db,
    )


# ── 调度循环 ──────────────────────────────────────────────────────────────────

async def service_gr_due_loop() -> None:
    """镜像 agreement_overdue_loop:睡到目标时间就直接跑,不在醒来后重算目标
    (重算会看到"已到点"而滚到明天,那个 bug 在 agreement_overdue 的测试里有
    完整记录)。"""
    while True:
        hour, minute = await _load_schedule()
        wait = _seconds_until_next_run(hour, minute)
        if wait > _RECHECK_SECONDS:
            await asyncio.sleep(_RECHECK_SECONDS)
            continue
        logger.info("Service GR due: next run in %.0f seconds (at %02d:%02dZ)", wait, hour, minute)
        await asyncio.sleep(wait)
        await run_service_gr_due()
