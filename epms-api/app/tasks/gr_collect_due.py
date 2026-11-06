"""
Collection SLA escalation (ISS-0146 ②, rule question 16 answered B — build what
the settings page describes).

EPMS → Admin → Collection Config, under "Collection Confirmation Required",
lists three timers counted from the day the physical GR was saved
(``company_config.collection_config``):
- Requester reminder (``reminder_days``): "Goods are ready to collect at
  Technical Warehouse for PO [#]." — to the holder of the open
  ``collect_goods`` task (the requester), every sweep while it stays open;
- Dept. Manager escalation (``manager_escalation_days``): "Goods for PO [#]
  have not been collected. Please follow up." — once, to the Department
  Manager of the requester's department, after a reminder was actually sent;
- Finance Manager alert (``fm_alert_days``): every Finance Manager, once, and
  the GR counts as "Collection Overdue" on the Warehouse dashboard
  (crud/dashboard.py, from collection_overdue_cutoff below).

The page says the timers are not active while collection is not required, so
the sweep runs only while ``collection_gate_enabled`` is on — no extra switch.
Daily at ``followup_time``. ``notification_logs`` is the "sent once" mark and
the reminded-first precondition (no migration), as in gr_ack_due.
Single-instance assumption, like the other sweeps.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.config import get_or_create as get_config
from app.db import session as session_module
from app.models.gr import GoodsReceipt
from app.models.task import Task
from app.models.user import User
from app.services.notification import (
    _build_email_html,
    _render,
    _send_with_retry,
    _smtp_kwargs,
    _task_link,
    dispatch_task_notification,
)
from app.tasks.daily_followup import (
    _RECHECK_SECONDS,
    _load_schedule,
    _seconds_until_next_run,
)
from app.tasks.gr_ack_due import _has_been_sent, _received_on

logger = logging.getLogger(__name__)

REMINDER_TEMPLATE = "gr_collect_reminder"
ESCALATION_TEMPLATE = "gr_collect_escalation"
FM_ALERT_TEMPLATE = "gr_collect_fm_alert"

_DEFAULTS = {"reminder_days": 2, "manager_escalation_days": 4, "fm_alert_days": 7}


@dataclass
class DueCollection:
    gr: GoodsReceipt
    task: Task
    days_waiting: int


def timers(cfg) -> dict[str, int] | None:
    """The three timers, or None while collection is not required."""
    cc = getattr(cfg, "collection_config", None) or {}
    if not cc.get("collection_gate_enabled", False):
        return None
    return {k: int(cc.get(k, v)) for k, v in _DEFAULTS.items()}


def collection_overdue_cutoff(cfg, now: datetime | None = None) -> datetime | None:
    """A collection_pending GR saved before this moment is "Collection Overdue"
    (past the Finance Manager alert); None while collection is not required."""
    t = timers(cfg)
    if t is None:
        return None
    now = now or datetime.now(timezone.utc)
    return now - timedelta(days=t["fm_alert_days"])


async def scan_uncollected(db: AsyncSession, *, reminder_days: int,
                           today: date | None = None) -> list[DueCollection]:
    """Physical GRs waiting for collection with an open collect_goods task held
    by a person, saved more than ``reminder_days`` days ago."""
    today = today or datetime.now(timezone.utc).date()
    rows = (await db.execute(
        select(GoodsReceipt, Task)
        .join(Task, (Task.document_id == GoodsReceipt.id) & (Task.document_type == "gr"))
        .where(
            GoodsReceipt.status == "collection_pending",
            Task.type == "collect_goods",
            Task.is_completed.is_(False),
            Task.assigned_user_id.is_not(None),
        )
        .order_by(GoodsReceipt.created_at)
    )).all()
    out: list[DueCollection] = []
    for gr, task in rows:
        days = (today - _received_on(gr)).days
        if days > reminder_days:
            out.append(DueCollection(gr=gr, task=task, days_waiting=days))
    return out


def _variables(cfg, due: DueCollection, recipient: User, holder: User | None) -> dict:
    return {
        "recipient_name": recipient.full_name or recipient.email,
        "holder_name": (holder.full_name or holder.email) if holder else "—",
        "gr_number": due.gr.number,
        "po_number": due.gr.po_number,
        "days_waiting": due.days_waiting,
        "vendor": due.gr.vendor_name or "—",
        "company_name": cfg.name,
        "link": _task_link("gr", due.gr.id),
    }


async def _email_once(db: AsyncSession, cfg, due: DueCollection, recipients: list[User],
                      template_key: str) -> bool:
    if await _has_been_sent(db, due.task.id, template_key):
        return False
    tpl = (cfg.email_templates or {}).get(template_key)
    if not tpl:
        return False
    from app.services.email import send_email

    holder = await db.get(User, due.task.assigned_user_id)
    sent = False
    for user in recipients:
        if not user.is_active or not user.email or user.notification_channel in ("none", "teams_only"):
            continue
        variables = _variables(cfg, due, user, holder)
        subject = _render(tpl["subject"], variables, escape=False)
        body = _build_email_html(_render(tpl["body"], variables, escape=True))
        await _send_with_retry(
            "email", due.task, user, template_key, db,
            send_fn=lambda u=user, s=subject, b=body: send_email(u.email, s, b, **_smtp_kwargs(cfg)),
            max_retries=3,
        )
        sent = True
    return sent


async def _department_manager(db: AsyncSession, due: DueCollection) -> User | None:
    from app.crud.gr import get_pr_requester_id
    from app.crud.pr import _get_dept_manager_id

    requester_id = await get_pr_requester_id(db, due.gr.pr_id) or due.task.assigned_user_id
    manager_id = await _get_dept_manager_id(db, requester_id, "dept_manager")
    if manager_id is None or manager_id == requester_id:
        return None
    return await db.get(User, manager_id)


async def _finance_managers(db: AsyncSession) -> list[User]:
    from app.core.access_scope import role_holder_ids

    ids = (await role_holder_ids(db, ("finance_manager",))).get("finance_manager", set())
    if not ids:
        return []
    return list((await db.execute(select(User).where(User.id.in_(ids)))).scalars())


async def run_gr_collect_due() -> None:
    logger.info("GR collection due: starting sweep")
    try:
        async with session_module.AsyncSessionLocal() as db:
            cfg = await get_config(db)
            t = timers(cfg)
            if t is None:
                logger.info("GR collection due: collection not required — timers inactive")
                await db.commit()
                return
            if (cfg.notification_settings or {}).get("default_channel") == "none":
                await db.commit()
                return
            due = await scan_uncollected(db, reminder_days=t["reminder_days"])
            logger.info("GR collection due: %d GR(s) waiting for collection", len(due))
            for item in due:
                reminded_before = await _has_been_sent(db, item.task.id, REMINDER_TEMPLATE)
                await dispatch_task_notification(
                    item.task, db,
                    template_key=REMINDER_TEMPLATE,
                    extra_vars={"gr_number": item.gr.number, "po_number": item.gr.po_number,
                                "days_waiting": item.days_waiting},
                )
                if item.days_waiting >= t["manager_escalation_days"] and reminded_before:
                    manager = await _department_manager(db, item)
                    if manager is not None:
                        await _email_once(db, cfg, item, [manager], ESCALATION_TEMPLATE)
                if item.days_waiting >= t["fm_alert_days"] and reminded_before:
                    await _email_once(db, cfg, item, await _finance_managers(db), FM_ALERT_TEMPLATE)
            await db.commit()
        logger.info("GR collection due: sweep finished")
    except Exception as exc:  # noqa: BLE001 — one failure must not kill the loop
        logger.error("GR collection due: sweep failed: %s", exc)


async def gr_collect_due_loop() -> None:
    """Same loop shape as gr_ack_due_loop."""
    while True:
        hour, minute = await _load_schedule()
        wait = _seconds_until_next_run(hour, minute)
        if wait > _RECHECK_SECONDS:
            await asyncio.sleep(_RECHECK_SECONDS)
            continue
        logger.info("GR collection due: next run in %.0f seconds (at %02d:%02dZ)", wait, hour, minute)
        await asyncio.sleep(wait)
        await run_gr_collect_due()
