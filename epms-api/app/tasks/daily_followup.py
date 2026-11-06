"""
Daily task digest — one email per person listing their open tasks.

Covers the task types the admin set to "digest" in Portal → Admin →
Notifications (services/task_notify_policy.py); "immediate" types are mailed by
app/tasks/task_notifier.py the moment they are created and are not repeated
here. Runs once a day at ``notification_settings.followup_time`` (UTC, default
08:00) via an asyncio background loop started in app/main.py lifespan.

Replaces the old per-task follow-up (one email per open task, every open task,
behind ``daily_followup_enabled``): whether a type takes part is now that
type's own setting, so the old switch is no longer read.
"""
from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.crud.config import get_or_create as get_config
from app.db import session as session_module
from app.models.notification_log import NotificationLog
from app.models.task import Task
from app.models.user import User
from app.services.notification import (
    _build_email_html, _smtp_kwargs, _task_link, resolve_task_recipients,
)
from app.services.task_notify_policy import effective_policy, label_for

logger = logging.getLogger(__name__)

DIGEST_TEMPLATE = "task_digest"
NEW_WINDOW = timedelta(hours=24)


@dataclass
class _Inbox:
    address: str
    greeting: str
    user: User | None = None
    tasks: list[Task] = field(default_factory=list)


def _digest_html(inbox: _Inbox, now: datetime) -> str:
    rows = []
    for t in sorted(inbox.tasks, key=lambda x: x.created_at, reverse=True):
        is_new = t.created_at and now - t.created_at <= NEW_WINDOW
        badge = (
            '<span style="background:#085E5E;color:#fff;border-radius:4px;'
            'padding:1px 6px;font-size:11px;margin-right:6px">NEW</span>'
            if is_new else ""
        )
        link = _task_link(t.document_type, t.document_id, task_type=t.type)
        cell = 'style="padding:6px 8px;border-bottom:1px solid #e5e7eb"'
        rows.append(
            '<tr>'
            f'<td {cell}>{badge}{html.escape(label_for(t.type))}</td>'
            f'<td {cell}><a href="{html.escape(link)}">{html.escape(t.document_number or "")}</a></td>'
            f'<td {cell}>{html.escape(t.title or "")}</td>'
            f'<td {cell}>{t.created_at.date().isoformat() if t.created_at else ""}</td>'
            '</tr>'
        )
    head = 'style="padding:6px 8px"'
    return (
        f'<p>Hi {html.escape(inbox.greeting)},</p>'
        f'<p>You have {len(inbox.tasks)} open task(s) waiting:</p>'
        '<table style="border-collapse:collapse;width:100%;font-size:13px">'
        '<tr style="text-align:left;color:#6b7280">'
        f'<th {head}>Task</th><th {head}>Document</th><th {head}>Details</th><th {head}>Created</th></tr>'
        + "".join(rows) + '</table>'
    )


async def _collect_inboxes(db, tasks: list[Task], ns: dict) -> dict[str, _Inbox]:
    """Group tasks by the address that would receive them, using exactly the
    recipient rules the immediate path uses (delegation, shared mailboxes)."""
    inboxes: dict[str, _Inbox] = {}
    for task in tasks:
        resolved = await resolve_task_recipients(db, task, ns)
        if resolved.shared_mailbox:
            key = resolved.shared_mailbox.lower()
            inbox = inboxes.setdefault(key, _Inbox(resolved.shared_mailbox, "team"))
            inbox.tasks.append(task)
            continue
        for user in resolved.users:
            # Personal preference is an opt-out: only email-capable channels.
            if (user.notification_channel or "email_only") not in ("email_only", "both"):
                continue
            if not user.email:
                continue
            key = user.email.lower()
            first = user.full_name.split()[0] if (user.full_name or "").strip() else "there"
            inbox = inboxes.setdefault(key, _Inbox(user.email, first, user))
            if task not in inbox.tasks:
                inbox.tasks.append(task)
    return inboxes


async def run_daily_digest() -> int:
    """Send today's digests. Returns how many emails went out."""
    from app.services.email import send_email

    logger.info("Task digest: starting run")
    sent = 0
    try:
        # 惰性属性访问而非 from-import:测试 conftest 会把
        # session_module.AsyncSessionLocal 重绑到测试库。
        async with session_module.AsyncSessionLocal() as db:
            cfg = await get_config(db)
            ns = cfg.notification_settings or {}
            if ns.get("default_channel") == "none":
                logger.info("Task digest: notifications disabled company-wide — skipping")
                return 0
            open_tasks = (await db.execute(
                select(Task).where(Task.is_completed.is_(False))
            )).scalars().all()
            digest_tasks = [
                t for t in open_tasks
                if effective_policy(ns, t.type) == (True, "digest")
            ]
            if not digest_tasks:
                logger.info("Task digest: no open task of a digest type — nothing to send")
                return 0
            now = datetime.now(timezone.utc)
            inboxes = await _collect_inboxes(db, digest_tasks, ns)
            for inbox in inboxes.values():
                subject = f"[UniOps] {len(inbox.tasks)} open task(s) waiting for you"
                body = _build_email_html(_digest_html(inbox, now))
                status, error = "ok", None
                try:
                    await send_email(inbox.address, subject, body, **_smtp_kwargs(cfg))
                    sent += 1
                except Exception as exc:  # noqa: BLE001 — one inbox can't sink the run
                    status, error = "failed", str(exc)
                    logger.warning("Task digest: email to %s failed: %s", inbox.address, exc)
                db.add(NotificationLog(
                    task_id=None,
                    user_id=inbox.user.id if inbox.user else None,
                    recipient_email=inbox.address,
                    channel="email",
                    template_key=DIGEST_TEMPLATE,
                    status=status,
                    error_message=error,
                    attempt=1,
                ))
            await db.commit()
            logger.info("Task digest: %d task(s) across %d inbox(es), %d sent",
                        len(digest_tasks), len(inboxes), sent)
    except Exception as exc:  # noqa: BLE001
        logger.error("Task digest error: %s", exc)
    return sent


def _seconds_until_next_run(hour: int = 8, minute: int = 0) -> float:
    """Calculate seconds until the next occurrence of HH:MM local time."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        # Already past today's run time — schedule for tomorrow
        from datetime import timedelta
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _parse_followup_time(value) -> tuple[int, int]:
    """Parse a ``"HH:MM"`` config value; anything unparseable → (8, 0)."""
    try:
        hh, mm = str(value).strip().split(":")
        hour, minute = int(hh), int(mm)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, AttributeError):
        pass
    logger.warning("Daily follow-up: invalid followup_time %r — falling back to 08:00Z", value)
    return 8, 0


async def _load_schedule() -> tuple[int, int]:
    """Read notification_settings.followup_time from company config → (hour, minute) UTC."""
    try:
        async with session_module.AsyncSessionLocal() as db:
            cfg = await get_config(db)
            raw = (cfg.notification_settings or {}).get("followup_time")
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — a broken DB must not kill the loop
        logger.error("Daily follow-up: failed to load schedule, using 08:00Z: %s", exc)
        return 8, 0
    return _parse_followup_time(raw) if raw is not None else (8, 0)


# Config poll cap: while far from the target time the loop only naps this long
# before re-reading followup_time, so an admin change applies within ~15 min
# instead of after the previously scheduled (up to 24 h away) run.
_RECHECK_SECONDS = 900


async def daily_followup_loop() -> None:
    """
    Infinite asyncio loop that fires run_daily_digest() once per day at the
    configured followup_time (UTC).

    Run as: asyncio.create_task(daily_followup_loop())
    """
    while True:
        hour, minute = await _load_schedule()
        wait = _seconds_until_next_run(hour, minute)
        if wait > _RECHECK_SECONDS:
            await asyncio.sleep(_RECHECK_SECONDS)
            continue
        logger.info("Daily follow-up: next run in %.0f seconds (at %02d:%02dZ)", wait, hour, minute)
        await asyncio.sleep(wait)
        await run_daily_digest()
