"""
Notification dispatcher — routes email/Teams notifications for task events.

Usage (fire-and-forget from API layer):
    import asyncio
    asyncio.create_task(
        dispatch_task_notification(task_id, db_session_factory)
    )
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_log import NotificationLog
from app.models.task import Task
from app.models.user import User
from app.models.config import CompanyConfig

logger = logging.getLogger(__name__)


# ── Template rendering ────────────────────────────────────────────────────────

def _render(template: str, variables: dict[str, Any]) -> str:
    """Replace {var} placeholders; unknown vars are left as-is."""
    def replace(m: re.Match) -> str:
        key = m.group(1)
        val = variables.get(key)
        if val is None:
            return m.group(0)
        return str(val)
    return re.sub(r"\{(\w+)\}", replace, template)


def _build_email_html(body_text: str) -> str:
    """Wrap plain-text/simple-HTML body in a basic styled container."""
    return f"""
    <div style="font-family:sans-serif;max-width:600px;margin:auto;color:#1a1a1a">
      <div style="background:#085E5E;padding:16px 24px;border-radius:8px 8px 0 0">
        <span style="color:#fff;font-size:18px;font-weight:700">EPMS</span>
      </div>
      <div style="background:#f9fafb;padding:24px;border-radius:0 0 8px 8px">
        {body_text.replace(chr(10), "<br>")}
      </div>
      <p style="font-size:11px;color:#999;text-align:center;margin-top:16px">
        This is an automated notification from EPMS.
      </p>
    </div>
    """


# ── SMTP config helper ────────────────────────────────────────────────────────

def _smtp_kwargs(cfg: CompanyConfig) -> dict:
    from app.core.config import settings
    return {
        "smtp_host": cfg.smtp_host or settings.SMTP_HOST,
        "smtp_port": cfg.smtp_port or settings.SMTP_PORT,
        "smtp_user": cfg.smtp_user or settings.SMTP_USER,
        "smtp_password": cfg.smtp_password or settings.SMTP_PASSWORD,
        "smtp_use_tls": cfg.smtp_use_tls if cfg.smtp_use_tls is not None else settings.SMTP_USE_TLS,
        "smtp_from": cfg.smtp_from or settings.SMTP_FROM,
    }


# ── Core dispatcher ───────────────────────────────────────────────────────────

async def dispatch_task_notification(
    task: Task,
    db: AsyncSession,
    *,
    template_key: str | None = None,
    extra_vars: dict[str, Any] | None = None,
    is_followup: bool = False,
    max_retries: int = 3,
) -> None:
    """
    Dispatch a notification for a single task.

    Resolves recipients, renders templates, sends via the user's preferred
    channel (email / teams / both / none), and logs each attempt.

    Non-blocking — all errors are caught and logged; never raises.
    """
    try:
        await _dispatch(
            task, db,
            template_key=template_key,
            extra_vars=extra_vars or {},
            is_followup=is_followup,
            max_retries=max_retries,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Unhandled error in dispatch_task_notification for task %s: %s", task.id, exc)


async def _dispatch(
    task: Task,
    db: AsyncSession,
    *,
    template_key: str,
    extra_vars: dict[str, Any],
    is_followup: bool,
    max_retries: int,
) -> None:
    from app.services.email import send_email
    from app.services.teams import send_teams_card
    from app.crud.config import get_or_create as get_config

    cfg = await get_config(db)

    # ── Resolve notification_settings + templates ───────────────────────────
    notif_settings: dict = cfg.notification_settings or {}
    email_templates: dict = cfg.email_templates or {}
    company_channel: str = notif_settings.get("default_channel", "email_only")
    teams_webhook: str | None = notif_settings.get("teams_webhook_url")

    tpl_key = template_key or _infer_template(task.type, is_followup)
    tpl: dict | None = email_templates.get(tpl_key)

    # ── Resolve recipients ──────────────────────────────────────────────────
    recipients: list[User] = []
    if task.assigned_user_id:
        user = await db.get(User, task.assigned_user_id)
        if user and user.is_active:
            recipients.append(user)
    else:
        # All active users with matching role
        result = await db.execute(
            select(User).where(User.role == task.assigned_role, User.is_active.is_(True))
        )
        recipients = list(result.scalars().all())

    if not recipients:
        logger.debug("No recipients for task %s (role=%s)", task.id, task.assigned_role)
        return

    # ── Common template variables ───────────────────────────────────────────
    system_url = "http://localhost:5173"  # overridden by notification_settings.system_url if set
    system_url = notif_settings.get("system_url", system_url)
    link = f"{system_url}/{task.document_type}/{task.document_id}"

    base_vars: dict[str, Any] = {
        "company_name": cfg.name,
        "document_type": task.document_type.upper(),
        "document_number": task.document_number,
        "link": link,
        # Convenience aliases so templates can use natural names
        "pr_number": task.document_number,
        "po_number": task.document_number,
        "pa_number": task.document_number,
        "gr_number": task.document_number,
        "vendor": task.vendor or "",
        "vendor_name": task.vendor or "",
        "amount": str(task.amount) if task.amount else "",
        "task_title": task.title,
        **extra_vars,
    }

    for user in recipients:
        channel = user.notification_channel or company_channel
        if channel == "none":
            continue

        user_vars = {**base_vars, "recipient_name": user.full_name.split()[0]}

        # ── Email ───────────────────────────────────────────────────────────
        if channel in ("email_only", "both") and user.email:
            if tpl:
                subject = _render(tpl.get("subject", task.title), user_vars)
                html_body = _render(tpl.get("body", task.description or ""), user_vars)
            else:
                subject = task.title
                html_body = task.description or task.title

            html = _build_email_html(html_body)
            await _send_with_retry(
                "email", task, user, tpl_key, db,
                send_fn=lambda: send_email(user.email, subject, html, **_smtp_kwargs(cfg)),
                max_retries=max_retries,
            )

        # ── Teams ───────────────────────────────────────────────────────────
        if channel in ("teams_only", "both") and user.teams_account and teams_webhook:
            if tpl:
                card_title = _render(tpl.get("subject", task.title), user_vars)
                card_body = _render(tpl.get("body", task.description or ""), user_vars)
            else:
                card_title = task.title
                card_body = task.description or task.title

            await _send_with_retry(
                "teams", task, user, tpl_key, db,
                send_fn=lambda: send_teams_card(teams_webhook, card_title, card_body, link=link),
                max_retries=max_retries,
            )


async def _send_with_retry(
    channel: str,
    task: Task,
    user: User,
    template_key: str,
    db: AsyncSession,
    *,
    send_fn,
    max_retries: int,
) -> None:
    """Try send_fn up to max_retries times with exponential backoff. Log each attempt."""
    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            await send_fn()
            db.add(NotificationLog(
                task_id=task.id,
                user_id=user.id,
                recipient_email=user.email,
                channel=channel,
                template_key=template_key,
                status="ok",
                attempt=attempt,
            ))
            await db.flush()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning("Notification attempt %d/%d failed (channel=%s, user=%s): %s",
                           attempt, max_retries, channel, user.id, exc)
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)   # 2s, 4s backoff

    # All attempts failed — log failure
    db.add(NotificationLog(
        task_id=task.id,
        user_id=user.id,
        recipient_email=user.email,
        channel=channel,
        template_key=template_key,
        status="failed",
        error_message=last_error,
        attempt=max_retries,
    ))
    await db.flush()


def _infer_template(task_type: str, is_followup: bool) -> str:
    """Map task type to a template key."""
    if is_followup:
        return "daily_pending_reminder"
    return {
        "approve_pr": "pr_approval_request",
        "revise_pr": "pr_returned",
        "create_po": "po_creation_request",
        "approve_po": "po_approval_request",
        "place_order": "po_place_order",
        "acknowledge_gr": "gr_created",
        "collect_goods": "gr_collection_ready",
        "confirm_service_gr": "service_gr_pending",
        "create_pa": "create_pa_reminder",
        "approve_pa": "pa_approval_request",
        "settle_prepayment": "prepayment_settlement_overdue",
    }.get(task_type, "pr_approval_request")


# ── Convenience fire-and-forget helper ───────────────────────────────────────

def fire_and_forget_notify(task: Task, db: AsyncSession, **kwargs) -> None:
    """
    Schedule notification dispatch as a background asyncio task.

    Call this AFTER db.commit() so the task record is persisted.
    The background coroutine creates its own DB session to avoid
    sharing the request session after it closes.
    """
    asyncio.create_task(_notify_in_background(task.id, **kwargs))


async def _notify_in_background(task_id, **kwargs) -> None:
    """Run in a fresh DB session so it doesn't share the request session."""
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import select as sa_select

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(sa_select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            if task is None:
                return
            await dispatch_task_notification(task, db, **kwargs)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("Background notification error for task %s: %s", task_id, exc)
