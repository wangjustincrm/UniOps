"""
GR acknowledgement reminder and escalation (ISS-0146 ①, PRD GR-N-002 / GR-N-003).

EPMS → Admin → GR Notification SLA (``company_config.gr_notification_sla``:
reminder_days, manager_escalation_days) was stored and shown with "these timers
control follow-up…" while nothing read it. This sweep makes it true.

What it chases: a goods receipt still waiting for the PR owner to acknowledge
it — an open ``acknowledge_gr`` task on a GR in ``pending_ack`` — once the GR is
older than ``reminder_days``. Collection (``collect_goods``) is not chased here:
that belongs to the Collection Config timers, which are not built.

Shape copied from ``service_gr_due``:
- daily at ``followup_time``; switch ``gr_ack_due_enabled`` (default OFF) and
  ``gr_ack_due_dry_run`` (default ON — the first runs only send System Admins
  the list it would chase);
- the reminder reuses the existing task (no new task type) with its own
  template, every sweep while the task stays open;
- the escalation goes once, to the Department Manager of the requester's
  department with a copy to the requester (PRD GR-N-003), and only after a
  reminder was actually sent — ``notification_logs`` is both the "sent once"
  mark and that precondition (no migration).

Single-instance assumption, like the other sweeps.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plant_time import plant_today
from app.crud.config import get_or_create as get_config
from app.db import session as session_module
from app.models.gr import GoodsReceipt
from app.models.notification_log import NotificationLog
from app.models.task import Task
from app.models.user import User
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

REMINDER_TEMPLATE = "gr_ack_reminder"
ESCALATION_TEMPLATE = "gr_ack_escalation"

_DEFAULT_REMINDER_DAYS = 1
_DEFAULT_MANAGER_ESCALATION_DAYS = 3


@dataclass
class DueAck:
    """A GR waiting for acknowledgement past the reminder threshold."""
    gr: GoodsReceipt
    task: Task
    days_waiting: int


def _received_on(gr: GoodsReceipt) -> date:
    """The plant's calendar day of the receipt (a naive stamp is UTC)."""
    return plant_today(gr.received_at or gr.created_at)


async def scan_unacknowledged(
    db: AsyncSession, *, reminder_days: int = _DEFAULT_REMINDER_DAYS, today: date | None = None,
) -> list[DueAck]:
    """GRs in pending_ack with an open acknowledge_gr task held by a person,
    received more than ``reminder_days`` days ago (strictly: the day the
    threshold is reached is not chased yet, same as service_gr_due)."""
    today = today or plant_today()
    rows = (await db.execute(
        select(GoodsReceipt, Task)
        .join(Task, (Task.document_id == GoodsReceipt.id) & (Task.document_type == "gr"))
        .where(
            GoodsReceipt.status == "pending_ack",
            Task.type == "acknowledge_gr",
            Task.is_completed.is_(False),
            Task.assigned_user_id.is_not(None),
        )
        .order_by(GoodsReceipt.created_at)
    )).all()
    out: list[DueAck] = []
    for gr, task in rows:
        days = (today - _received_on(gr)).days
        if days > reminder_days:
            out.append(DueAck(gr=gr, task=task, days_waiting=days))
    return out


async def _has_been_sent(db: AsyncSession, task_id, template_key: str) -> bool:
    return bool((await db.execute(
        select(NotificationLog.id).where(
            NotificationLog.task_id == task_id,
            NotificationLog.template_key == template_key,
            NotificationLog.status == "ok",
        ).limit(1)
    )).scalar_one_or_none())


async def _escalate_to_manager(db: AsyncSession, cfg, due: DueAck) -> bool:
    """PRD GR-N-003: email the Department Manager of the requester's
    department, with a copy to the requester. Once."""
    if await _has_been_sent(db, due.task.id, ESCALATION_TEMPLATE):
        return False

    from app.crud.gr import get_pr_requester_id
    from app.crud.pr import _get_dept_manager_id
    from app.services.email import send_email

    holder_id = due.task.assigned_user_id
    requester_id = await get_pr_requester_id(db, due.gr.pr_id) or holder_id
    manager_id = await _get_dept_manager_id(db, requester_id, "dept_manager")
    if manager_id is None or manager_id == requester_id:
        return False
    manager = await db.get(User, manager_id)
    if manager is None or not manager.is_active or not manager.email:
        return False
    requester = await db.get(User, requester_id)
    cc = (requester.email if requester is not None and requester.is_active
          and requester.email and requester.email != manager.email else None)
    tpl = (cfg.email_templates or {}).get(ESCALATION_TEMPLATE)
    if not tpl:
        return False
    holder = await db.get(User, holder_id)
    variables = {
        "recipient_name": manager.full_name or manager.email,
        "holder_name": (holder.full_name or holder.email) if holder else "—",
        "gr_number": due.gr.number,
        "po_number": due.gr.po_number,
        "days_waiting": due.days_waiting,
        "vendor": due.gr.vendor_name or "—",
        "company_name": cfg.name,
        "link": _task_link("gr", due.gr.id),
    }
    subject = _render(tpl["subject"], variables, escape=False)
    html = _build_email_html(_render(tpl["body"], variables, escape=True))
    await _send_with_retry(
        "email", due.task, manager, ESCALATION_TEMPLATE, db,
        send_fn=lambda: send_email(manager.email, subject, html, cc=cc, **_smtp_kwargs(cfg)),
        max_retries=3,
    )
    return True


async def run_gr_ack_due() -> None:
    logger.info("GR ack due: starting sweep")
    try:
        async with session_module.AsyncSessionLocal() as db:
            cfg = await get_config(db)
            ns = cfg.notification_settings or {}
            if not ns.get("gr_ack_due_enabled", False):
                logger.info("GR ack due: disabled by admin toggle — skipping run")
                await db.commit()
                return
            sla = cfg.gr_notification_sla or {}
            reminder_days = int(sla.get("reminder_days", _DEFAULT_REMINDER_DAYS))
            escalation_days = int(sla.get("manager_escalation_days", _DEFAULT_MANAGER_ESCALATION_DAYS))
            dry_run = ns.get("gr_ack_due_dry_run", True)

            due = await scan_unacknowledged(db, reminder_days=reminder_days)
            logger.info("GR ack due: %d GR(s) waiting for acknowledgement", len(due))
            if not due:
                await db.commit()
                return
            if dry_run:
                await _report_dry_run(db, due)
                await db.commit()
                return

            for item in due:
                # Read BEFORE sending this round's reminder: dispatch writes a
                # notification_logs row, after which this would always be True.
                reminded_before = await _has_been_sent(db, item.task.id, REMINDER_TEMPLATE)
                await dispatch_task_notification(
                    item.task, db,
                    template_key=REMINDER_TEMPLATE,
                    extra_vars={
                        "gr_number": item.gr.number,
                        "po_number": item.gr.po_number,
                        "days_waiting": item.days_waiting,
                    },
                )
                if item.days_waiting >= escalation_days and reminded_before:
                    await _escalate_to_manager(db, cfg, item)
            await db.commit()
        logger.info("GR ack due: sweep finished")
    except Exception as exc:  # noqa: BLE001 — one failure must not kill the loop
        logger.error("GR ack due: sweep failed: %s", exc)


async def _report_dry_run(db: AsyncSession, due: list[DueAck]) -> None:
    """Dry run: nobody is reminded; System Admins get the list."""
    import html as _html

    lines = "".join(
        f"<li>{_html.escape(d.gr.number)} — PO {_html.escape(d.gr.po_number or '')}, "
        f"waiting {d.days_waiting} day(s)</li>"
        for d in due
    )
    await send_admin_alert(
        f"[Dry run] {len(due)} goods receipt(s) waiting for acknowledgement",
        "<p>The GR acknowledgement reminder is in <b>dry-run</b> mode, so nobody "
        f"was reminded. These receipts would have been chased:</p><ul>{lines}</ul>"
        "<p>Turn off <b>GR Acknowledgement Reminder — Dry Run</b> in Portal → Admin → "
        "Notification Settings once this list looks right.</p>",
        db,
    )


async def gr_ack_due_loop() -> None:
    """Same loop shape as service_gr_due_loop."""
    while True:
        hour, minute = await _load_schedule()
        wait = _seconds_until_next_run(hour, minute)
        if wait > _RECHECK_SECONDS:
            await asyncio.sleep(_RECHECK_SECONDS)
            continue
        logger.info("GR ack due: next run in %.0f seconds (at %02d:%02d plant time)", wait, hour, minute)
        await asyncio.sleep(wait)
        await run_gr_ack_due()
