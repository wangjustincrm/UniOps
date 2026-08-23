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

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.notification_log import NotificationLog
from app.models.task import Task
from app.models.user import User
from app.models.config import CompanyConfig

logger = logging.getLogger(__name__)


# ── Per-module deep-link resolution ─────────────────────────────────────────────
# This notifier is system-wide (see settings): the shared tasks table holds tasks
# from every module, so an email link must land in the RIGHT module's frontend.
# approval-api is the sole writer; the document_types it emits are the fixed set
# below plus OA expense-claim types (document_type = claim_type.lower(), which is
# open-ended — custom forms included). Everything not explicitly mapped is
# therefore an OA expense claim and deep-links to OA's /expenses/{id}.
def _task_link(document_type: str, document_id: Any, task_type: str | None = None) -> str:
    # confirm_receipt: route the receiver straight to the New GR page, prefilled
    # with the PO (frontend already supports ?poId=).
    if task_type == "confirm_receipt":
        return f"{settings.EPMS_URL}/gr/new?poId={document_id}"
    dt = (document_type or "").lower()
    routes: dict[str, tuple[str, str]] = {
        "pr":          (settings.EPMS_URL,    "/pr/{id}"),
        "po":          (settings.EPMS_URL,    "/po/{id}"),
        "pa":          (settings.EPMS_URL,    "/pa/{id}"),
        "gr":          (settings.EPMS_URL,    "/gr/{id}"),
        "invoice":     (settings.EPMS_URL,    "/invoices/{id}"),
        # Purchase Agreement. Missing here since agr tasks were introduced, so
        # confirm_period / chase_agreement_invoice emails fell through to the
        # OA expense-claim default below and deep-linked to /expenses/<agr id>,
        # which 404s.
        "agr":         (settings.EPMS_URL,    "/agreements/{id}"),
        "pa_dir":      (settings.OA_URL,      "/pa/{id}"),          # OA Direct PA detail
        "budget_plan": (settings.FINANCE_URL, "/budget/plans/{id}"),
        "vms_visit":   (settings.VMS_URL,     ""),                  # VMS routes by role from its root
        "vms_train":   (settings.VMS_URL,     ""),
        "vms_ppe":     (settings.VMS_URL,     ""),
    }
    base, path = routes.get(dt, (settings.OA_URL, "/expenses/{id}"))  # default: OA expense claim
    return f"{base}{path.format(id=document_id)}"


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


def _shared_mailbox_for(notif_settings: dict, role: str | None) -> str | None:
    """共享邮箱地址(角色池任务专用);未配置/空串返回 None。"""
    if not role:
        return None
    mapping = notif_settings.get("role_shared_mailboxes") or {}
    if not isinstance(mapping, dict):
        return None
    addr = mapping.get(role)
    if isinstance(addr, str) and addr.strip():
        return addr.strip()
    return None


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
    from app.crud.config import get_or_create as get_config, role_display_name

    cfg = await get_config(db)

    # ── Resolve notification_settings + templates ───────────────────────────
    notif_settings: dict = cfg.notification_settings or {}
    email_templates: dict = cfg.email_templates or {}
    company_channel: str = notif_settings.get("default_channel", "email_only")
    teams_webhook: str | None = notif_settings.get("teams_webhook_url")

    # The company default_channel is a master switch: when set to "none",
    # notifications are suppressed for everyone, regardless of per-user
    # notification_channel. Per-user is NOT NULL (server default 'email_only'),
    # so `user.notification_channel or company_channel` would otherwise always
    # fall on the per-user value and silently defeat "disable notifications".
    if company_channel == "none":
        logger.info(
            "Notifications disabled company-wide (default_channel=none); skipping task %s",
            task.id,
        )
        return

    tpl_key = template_key or _infer_template(task.type, is_followup)
    tpl: dict | None = email_templates.get(tpl_key)

    # ── Resolve recipients ──────────────────────────────────────────────────
    # 角色池任务(无具体指派人)若为该角色配了共享邮箱,则整封只发共享邮箱:
    # 不再逐人发邮件、不发 Teams、也不看个人 notification_channel。
    shared_mailbox = (
        _shared_mailbox_for(notif_settings, task.assigned_role)
        if task.assigned_user_id is None
        else None
    )

    # 共享邮箱路径只发邮件(不发 Teams),因此显式配成 teams_only 时整条通知不发。
    # 注意这里是“只在 teams_only 时抑制”,而不是“只在 email_only/both 时发送”:
    # 存储值若是意外/拼错的渠道,仍然应该发出邮件,与逐人路径的默认 email_only 一致。
    # ("none" 是主开关,已在上面提前 return。)
    if shared_mailbox and company_channel == "teams_only":
        logger.info(
            "Shared mailbox configured for role %s but company default_channel=%s "
            "excludes email; skipping task %s",
            task.assigned_role, company_channel, task.id,
        )
        return

    recipients: list[User] = []
    if shared_mailbox is None:
        if task.assigned_user_id:
            recipient_id = task.assigned_user_id
            # Substitution, not widening: the delegator is away, so mailing
            # them is noise. active_delegate_id already returns None when the
            # stand-in is deactivated, which falls back to the delegator.
            if (task.type or "").startswith("approve"):
                from app.core.delegation import active_delegate_id
                stand_in = await active_delegate_id(db, task.assigned_user_id)
                if stand_in is not None:
                    recipient_id = stand_in
            user = await db.get(User, recipient_id)
            if user and user.is_active:
                recipients.append(user)
        elif task.assigned_role == "requester":
            # 'requester' 不是角色池:requester 任务没有具体受理人 = 单据丢了 PR
            # 链(导入 PO)。走池群发会给全公司每个 requester 发邮件(2026-08-05:
            # PMS 导入 PO 的 GR ack 群发 59 人×2 轮)。抑制群发,改为报警 admin。
            logger.warning(
                "Requester task %s (%s %s) has no assignee — suppressing role-wide "
                "fan-out, alerting admins instead",
                task.id, task.type, task.document_number,
            )
            doc_link = _task_link(task.document_type, task.document_id, task_type=task.type)
            subject = f"[EPMS] Requester task has no assignee — {task.document_number}"
            body = (
                f"Task <b>{task.title}</b> for {task.document_type.upper()} "
                f"<b>{task.document_number}</b> is addressed to the requester role but has "
                f"no concrete assignee — the document has no linked PR (typically an "
                f"imported PO), so there is no requester to notify.\n\n"
                f"The role-wide email fan-out was suppressed. Please review the document "
                f"and either link its PR or reassign the task.\n\n"
                f'<a href="{doc_link}">Open document</a>'
            )
            for admin in await _admin_recipients(db):
                await _send_with_retry(
                    "email", task, admin, "admin_requester_broadcast_alert", db,
                    send_fn=(lambda a=admin: send_email(
                        a.email, subject, _build_email_html(body), **_smtp_kwargs(cfg))),
                    max_retries=max_retries,
                )
            return
        else:
            # All active holders of the role — PRIMARY (users.role) ∪ ADDITIONAL
            # (identity's user_roles, same physical DB). Mirrors the Task Inbox's
            # get_for_role role union (access_scope._effective_role_codes): a
            # broadcast task (a singleton post like gm/opm, or a role pool) is
            # visible in-app to every holder, INCLUDING those who hold the role as
            # a SECONDARY role — so the email fan-out must reach the same set, or a
            # GM/OPM holding the post as an additional role sees it in-app but gets
            # no email.
            secondary_ids = (await db.execute(
                text("SELECT user_id FROM user_roles WHERE role_code = :rc"),
                {"rc": task.assigned_role},
            )).scalars().all()
            result = await db.execute(
                select(User).where(
                    User.is_active.is_(True),
                    or_(User.role == task.assigned_role, User.id.in_(secondary_ids)),
                )
            )
            recipients = list(result.scalars().all())

        if not recipients:
            logger.debug("No recipients for task %s (role=%s)", task.id, task.assigned_role)
            return

    # ── Common template variables ───────────────────────────────────────────
    # Deep-link resolves per module from the document_type (this notifier serves
    # every module, not just EPMS — see _task_link).
    link = _task_link(task.document_type, task.document_id, task_type=task.type)

    base_vars: dict[str, Any] = {
        "company_name": cfg.name,
        "document_type": task.document_type.upper(),
        "document_number": task.document_number,
        "link": link,
        "po_id": str(task.document_id) if task.document_type == "po" else "",
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

    if shared_mailbox:
        team_vars = {
            **base_vars,
            "recipient_name": f"{role_display_name(cfg, task.assigned_role)} Team",
        }
        if tpl:
            subject = _render(tpl.get("subject", task.title), team_vars)
            html_body = _render(tpl.get("body", task.description or ""), team_vars)
        else:
            subject = task.title
            html_body = _render(task.description or task.title, team_vars)

        html = _build_email_html(html_body)
        await _send_with_retry(
            "email", task, None, tpl_key, db,
            send_fn=lambda: send_email(shared_mailbox, subject, html, **_smtp_kwargs(cfg)),
            max_retries=max_retries,
            recipient_email=shared_mailbox,
        )
        return

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
                # 与共享邮箱分支保持一致:无模板时也要渲染占位符,
                # 否则收件人会看到字面量 {recipient_name}。
                html_body = _render(task.description or task.title, user_vars)

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
                card_body = _render(task.description or task.title, user_vars)

            await _send_with_retry(
                "teams", task, user, tpl_key, db,
                send_fn=lambda: send_teams_card(teams_webhook, card_title, card_body, link=link),
                max_retries=max_retries,
            )


async def _send_with_retry(
    channel: str,
    task: Task,
    user: User | None,
    template_key: str,
    db: AsyncSession,
    *,
    send_fn,
    max_retries: int,
    recipient_email: str | None = None,
) -> None:
    """Try send_fn up to max_retries times with exponential backoff. Log each attempt.

    ``user`` is None for shared-mailbox deliveries — the log row then carries only
    the recipient address (notification_logs.user_id is nullable).
    """
    to_addr = recipient_email if recipient_email is not None else (user.email if user else None)
    user_id = user.id if user else None
    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            await send_fn()
            db.add(NotificationLog(
                task_id=task.id,
                user_id=user_id,
                recipient_email=to_addr,
                channel=channel,
                template_key=template_key,
                status="ok",
                attempt=attempt,
            ))
            await db.flush()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning("Notification attempt %d/%d failed (channel=%s, recipient=%s): %s",
                           attempt, max_retries, channel, to_addr, exc)
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)   # 2s, 4s backoff

    # All attempts failed — log failure
    db.add(NotificationLog(
        task_id=task.id,
        user_id=user_id,
        recipient_email=to_addr,
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
        "confirm_receipt": "confirm_receipt",
        "approve_pa": "pa_approval_request",
        "settle_prepayment": "prepayment_settlement_overdue",
        "match_invoice": "match_invoice_assigned",
        "review_match": "match_review_request",
        "resolve_exception": "exception_resolution_request",
    }.get(task_type, "pr_approval_request")


# ── Admin alerting ────────────────────────────────────────────────────────────

async def _admin_recipients(db: AsyncSession) -> list[User]:
    """Active system_admin holders (primary users.role ∪ additional user_roles)
    that have an email address."""
    secondary_ids = (await db.execute(
        text("SELECT user_id FROM user_roles WHERE role_code = 'system_admin'")
    )).scalars().all()
    result = await db.execute(
        select(User).where(
            User.is_active.is_(True),
            or_(User.role == "system_admin", User.id.in_(secondary_ids)),
        )
    )
    return [u for u in result.scalars().all() if u.email]


async def send_admin_alert(subject: str, body_html: str, db: AsyncSession | None = None) -> None:
    """Email every active system admin. Never raises; honors the company-wide
    default_channel='none' master switch. Task-independent (no NotificationLog)."""
    try:
        if db is None:
            from app.db.session import AsyncSessionLocal
            async with AsyncSessionLocal() as session:
                await send_admin_alert(subject, body_html, db=session)
            return

        from app.services.email import send_email
        from app.crud.config import get_or_create as get_config

        cfg = await get_config(db)
        if (cfg.notification_settings or {}).get("default_channel") == "none":
            logger.info("Admin alert suppressed (default_channel=none): %s", subject)
            return
        html = _build_email_html(body_html)
        for admin in await _admin_recipients(db):
            try:
                await send_email(admin.email, subject, html, **_smtp_kwargs(cfg))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Admin alert to %s failed: %s", admin.email, exc)
    except Exception as exc:  # noqa: BLE001
        logger.error("send_admin_alert failed (%s): %s", subject, exc)


def fire_and_forget_admin_alert(subject: str, body_html: str) -> None:
    """Schedule an admin alert email in the background (own DB session)."""
    from app.core.background import spawn
    spawn(send_admin_alert(subject, body_html), name=f"admin_alert:{subject[:40]}")


# ── Convenience fire-and-forget helper ───────────────────────────────────────

def fire_and_forget_notify(task: Task, db: AsyncSession, **kwargs) -> None:
    """
    Schedule notification dispatch as a background asyncio task.

    Call this AFTER db.commit() so the task record is persisted.
    The background coroutine creates its own DB session to avoid
    sharing the request session after it closes.
    """
    from app.core.background import spawn
    spawn(_notify_in_background(task.id, **kwargs), name=f"notify:{task.id}")


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
