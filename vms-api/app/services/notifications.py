"""Email notifications (PRD §2.1.2.1 / VMS-PR-021..023).

Two automatic notifications fire when a visit needing training / PPE is
confirmed:
  - Training Contact (HR-designated) — visitor requires food-safety training
  - PPE Contact (Janitor-designated) — visitor requires PPE issuance

Trigger rule (PRD VMS-PR-007): GMP-zone + Laboratory areas need BOTH.

Implementation notes:
  - SMTP credentials come from the SAME source EPMS uses — `company_config`
    table. We don't replicate them in vms-api settings.
  - `smtplib` (sync stdlib) is wrapped in `asyncio.to_thread()` — no extra
    deps needed, fine for our low volume.
  - When SMTP isn't configured, we LOG the intended email and return without
    error. This is the "smoke-test mode" for dev and the graceful-degrade
    for prod misconfigs (visit still confirms, audit log records the attempt).
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import uuid
from email.message import EmailMessage
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_mirror import User
from app.models.visit import AccessArea, Visit
from app.models.visitor import Visitor
from app.models.vms_config import VmsConfig

log = logging.getLogger(__name__)

# Areas that trigger training + PPE notifications.
_NEEDS_TRAINING_AND_PPE = frozenset({
    AccessArea.production_gmp,
    AccessArea.laboratory,
    AccessArea.all,
})


# ── SMTP config (read from epms-api's company_config, same DB) ──────────────-

async def _load_smtp_config(db: AsyncSession) -> dict[str, Any]:
    """Resolve SMTP creds for outbound VMS email.

    Lookup order:
      1. `vms_config.smtp_settings` JSONB (VMS Admin panel — preferred).
      2. `public.company_config` (shared EPMS / OA SMTP — legacy fallback).

    Returns a dict with keys: host, port, user, password, use_tls, from_email.
    Missing fields are None; caller decides what to do (`_send_email` skips
    delivery when `host` is missing).
    """
    vms_row = (
        await db.execute(text("SELECT smtp_settings FROM vms_config LIMIT 1"))
    ).scalar_one_or_none()
    vms_cfg = vms_row if isinstance(vms_row, dict) else {}
    if vms_cfg.get("host"):
        return {
            "host":       vms_cfg.get("host"),
            "port":       vms_cfg.get("port"),
            "user":       vms_cfg.get("user"),
            "password":   vms_cfg.get("password"),
            "use_tls":    vms_cfg.get("use_tls"),
            "from_email": vms_cfg.get("from_email"),
        }

    # Fallback: shared epms-api SMTP. Same raw-SQL access pattern we used
    # before — vms-api doesn't model the full CompanyConfig.
    row = (
        await db.execute(
            text(
                "SELECT smtp_host, smtp_port, smtp_user, smtp_password, "
                "smtp_use_tls, smtp_from FROM company_config LIMIT 1"
            )
        )
    ).first()
    if row is None:
        return {}
    return {
        "host":       row[0],
        "port":       row[1],
        "user":       row[2],
        "password":   row[3],
        "use_tls":    row[4],
        "from_email": row[5],
    }


# ── SMTP send (blocking call, hidden behind to_thread) ──────────────────────-

def _send_smtp_blocking(*, cfg: dict[str, Any], to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = cfg.get("from_email") or cfg.get("user") or "vms@example.com"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    host = cfg["host"]
    port = int(cfg.get("port") or 25)
    use_tls = bool(cfg.get("use_tls"))

    if use_tls:
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.starttls()
            if cfg.get("user"):
                s.login(cfg["user"], cfg.get("password") or "")
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=10) as s:
            if cfg.get("user"):
                s.login(cfg["user"], cfg.get("password") or "")
            s.send_message(msg)


async def _send_email(*, cfg: dict[str, Any], to: str, subject: str, body: str) -> bool:
    """Returns True if SMTP delivery was attempted, False if skipped (no config).

    Exceptions during delivery are caught and logged — they don't fail the
    triggering visit. This is intentional: the visit succeeds; an unreachable
    SMTP server is an ops problem, not a user-blocking one.
    """
    if not cfg.get("host"):
        log.warning(
            "Skipping email to %s (subject=%r): no SMTP host configured",
            to, subject,
        )
        return False
    try:
        await asyncio.to_thread(_send_smtp_blocking, cfg=cfg, to=to, subject=subject, body=body)
        log.info("Sent notification to %s (subject=%r)", to, subject)
        return True
    except Exception:
        log.exception("Failed to send notification to %s (subject=%r)", to, subject)
        return False


# ── Public dispatch entry point ─────────────────────────────────────────────-

def _format_training_email(visit: Visit, visitor: Visitor, host: User | None) -> tuple[str, str]:
    subject = (
        f"VMS — Food-safety training required: visitor {visitor.first_name} "
        f"{visitor.last_name}"
    )
    body = (
        f"A visit has been registered that requires food-safety training "
        f"briefing before the visitor can enter GMP / Laboratory zones.\n\n"
        f"Visitor:        {visitor.first_name} {visitor.last_name}\n"
        f"Company:        {visitor.company_name}\n"
        f"Visit date:     {visit.visit_date.isoformat()}\n"
        f"Planned arrival:{visit.planned_arrival.isoformat()}\n"
        f"Access area:    {visit.access_area.value.replace('_', ' ')}\n"
        f"Host:           {host.full_name if host else '—'}\n"
        f"Visit ID:       {visit.id}\n\n"
        f"Please contact the Host to confirm training arrangements.\n"
    )
    return subject, body


def _format_ppe_email(visit: Visit, visitor: Visitor, host: User | None) -> tuple[str, str]:
    subject = (
        f"VMS — PPE issuance required: visitor {visitor.first_name} {visitor.last_name}"
    )
    body = (
        f"A visit has been registered that requires PPE issuance before the "
        f"visitor enters production / laboratory areas.\n\n"
        f"Visitor:        {visitor.first_name} {visitor.last_name}\n"
        f"Company:        {visitor.company_name}\n"
        f"Visit date:     {visit.visit_date.isoformat()}\n"
        f"Planned arrival:{visit.planned_arrival.isoformat()}\n"
        f"Access area:    {visit.access_area.value.replace('_', ' ')}\n"
        f"Host:           {host.full_name if host else '—'}\n"
        f"Visit ID:       {visit.id}\n\n"
        f"Standard kit: hard hat, food-safe smock, hair net, shoe covers.\n"
        f"Adjust per the access area's requirements.\n"
    )
    return subject, body


def _format_ppe_item_lines(item: dict, visitor_label: str) -> list[str]:
    """Render one visitor's PPE block as plain-text lines."""
    clothing_size = item.get("clothing_size") or "—"
    if clothing_size == "other":
        clothing_size = f"other ({item.get('clothing_size_other') or 'not specified'})"

    footwear = item.get("footwear")
    if footwear == "shoes":
        shoe_size = item.get("shoe_size") or "—"
        if shoe_size == "other":
            shoe_size = f"other ({item.get('shoe_size_other') or 'not specified'})"
        footwear_line = f"  Footwear:      safety shoes (size {shoe_size})"
    elif footwear == "shoe_covers":
        footwear_line = "  Footwear:      shoe covers"
    else:
        footwear_line = "  Footwear:      —"

    return [
        f"- {visitor_label}",
        f"  Clothing size: {clothing_size}",
        footwear_line,
    ]


async def _format_ppe_request_email(
    db: AsyncSession,
    visit: Visit,
    visitor: Visitor,
    host: User | None,
) -> tuple[str, str]:
    """Janitor PPE prep email — one block per visitor on the appointment.

    Fires after the visit is approved (or immediately for visits that don't
    need approval). The body lists the exact gear the Host requested for
    each person so the Janitor can pre-stage everything in one trip.
    """
    req = visit.ppe_requested or {}
    items = req.get("items") or []

    # Resolve visitor display names so the Janitor sees who's who. The
    # request payload only has IDs.
    visitor_ids: list[uuid.UUID] = []
    for it in items:
        vid_raw = it.get("visitor_id")
        if not vid_raw:
            continue
        try:
            visitor_ids.append(uuid.UUID(str(vid_raw)))
        except (ValueError, TypeError):
            continue
    name_by_id: dict[uuid.UUID, str] = {}
    if visitor_ids:
        rows = (await db.execute(
            select(Visitor).where(Visitor.id.in_(visitor_ids))
        )).scalars().all()
        for v in rows:
            name_by_id[v.id] = f"{v.first_name} {v.last_name} ({v.company_name})"

    lines: list[str] = []
    if items:
        for it in items:
            try:
                vid = uuid.UUID(str(it.get("visitor_id"))) if it.get("visitor_id") else None
            except (ValueError, TypeError):
                vid = None
            label = name_by_id.get(vid) if vid else None
            label = label or "(visitor)"
            lines.extend(_format_ppe_item_lines(it, label))
            lines.append("")
    else:
        lines.append("(no per-visitor PPE entries)")

    extra_notes = (req.get("notes") or "").strip()
    notes_line = f"\nHost notes: {extra_notes}\n" if extra_notes else ""

    visitor_count = len(items)
    suffix = "" if visitor_count == 1 else f" (+{visitor_count - 1} more)"
    subject = (
        f"VMS — PPE prep needed for {visitor.first_name} {visitor.last_name}{suffix} "
        f"({visit.visit_date.isoformat()})"
    )
    body = (
        f"A visit has been approved and the Host requested PPE staging.\n\n"
        f"Visit date:     {visit.visit_date.isoformat()}\n"
        f"Planned arrival:{visit.planned_arrival.isoformat()}\n"
        f"Access area:    {visit.access_area.value.replace('_', ' ')}\n"
        f"Host:           {host.full_name if host else '—'}\n\n"
        f"PPE requested:\n"
        + "\n".join(lines)
        + f"{notes_line}\n"
        f"Please pre-stage the gear before the visitor's planned arrival.\n"
        f"\nVisit ID: {visit.id}\n"
    )
    return subject, body


async def notify_janitor_ppe_request(
    db: AsyncSession,
    *,
    visit: Visit,
    visitor: Visitor,
    host: User | None,
) -> bool:
    """Send the PPE-prep email to the configured Janitor contact.

    Caller decides *when* to fire (visit auto-confirms at create time, or
    visit clears approval at read time) and persists the idempotency flag
    (`visit.ppe_notified_at`). Returns True if SMTP delivery was attempted.
    """
    if not visit.ppe_requested:
        return False

    cfg = (await db.execute(
        text("SELECT notification_contacts FROM vms_config LIMIT 1")
    )).scalar_one_or_none()
    contacts: dict = cfg if isinstance(cfg, dict) else {}
    ppe_email = contacts.get("ppe_email")
    if not ppe_email:
        log.info(
            "Visit %s requests PPE but no janitor email is configured", visit.id,
        )
        return False

    smtp_cfg = await _load_smtp_config(db)
    subj, body = await _format_ppe_request_email(db, visit, visitor, host)
    return await _send_email(cfg=smtp_cfg, to=ppe_email, subject=subj, body=body)


def _format_host_approval_email(
    visit: Visit, visitor: Visitor, approved: bool,
) -> tuple[str, str]:
    """Subject + body for the email sent to the Host once approval resolves."""
    name = f"{visitor.first_name} {visitor.last_name}"
    if approved:
        subject = f"VMS — Visit approved: {name}"
        body = (
            f"Your visit appointment has been approved.\n\n"
            f"Visitor:     {name}\n"
            f"Company:     {visitor.company_name}\n"
            f"Visit date:  {visit.visit_date.isoformat()}\n"
            f"Access area: {visit.access_area.value.replace('_', ' ')}\n"
            f"Visit ID:    {visit.id}\n\n"
            f"You can now print the visitor badge when the guest arrives.\n"
        )
    else:
        subject = f"VMS — Visit rejected: {name}"
        body = (
            f"Your visit appointment has been rejected by an approver.\n\n"
            f"Visitor:     {name}\n"
            f"Company:     {visitor.company_name}\n"
            f"Visit date:  {visit.visit_date.isoformat()}\n"
            f"Access area: {visit.access_area.value.replace('_', ' ')}\n"
            f"Visit ID:    {visit.id}\n\n"
            f"Please cancel the visit in VMS, or reschedule with a different access area.\n"
        )
    return subject, body


async def notify_host_approval_resolved(
    db: AsyncSession,
    *,
    visit: Visit,
    visitor: Visitor,
    host: User,
    approved: bool,
) -> bool:
    """Email the Host when their GMP visit's approval clears (approved or
    rejected). Returns True if delivery was attempted, False if skipped.

    Caller is responsible for idempotency — fire from a single transition
    site (the read-side `sync_status_from_approval` is a natural fit) and
    persist a flag so a second read doesn't re-send.
    """
    if not host or not host.email:
        return False
    smtp_cfg = await _load_smtp_config(db)
    subject, body = _format_host_approval_email(visit, visitor, approved=approved)
    return await _send_email(cfg=smtp_cfg, to=host.email, subject=subject, body=body)


# ── Scheduler emails (reminders / overdue alerts) ───────────────────────────-

def _fmt_dt(value) -> str:
    """Render a datetime/date for email bodies; empty string when None."""
    return value.isoformat() if value is not None else "—"


async def notify_host_visit_reminder(
    db: AsyncSession, *, visit: Visit, visitor: Visitor, host: User | None,
) -> bool:
    """Day-before reminder to the Host (PRD VMS-PR-012).

    Fired by the scheduler the calendar day before `visit_date`. Returns True
    if delivery was attempted (caller persists `visit.reminder_sent_at`).
    """
    if not host or not host.email:
        return False
    name = f"{visitor.first_name} {visitor.last_name}"
    subject = f"VMS — Reminder: visit tomorrow — {name} ({visitor.company_name})"
    body = (
        f"This is a reminder that you are hosting a visitor tomorrow.\n\n"
        f"Visitor:        {name}\n"
        f"Company:        {visitor.company_name}\n"
        f"Visit date:     {_fmt_dt(visit.visit_date)}\n"
        f"Planned arrival:{_fmt_dt(visit.planned_arrival)}\n"
        f"Access area:    {visit.access_area.value.replace('_', ' ')}\n"
        f"Visit ID:       {visit.id}\n\n"
        f"Print the visitor badge from VMS when the guest arrives.\n"
    )
    smtp_cfg = await _load_smtp_config(db)
    return await _send_email(cfg=smtp_cfg, to=host.email, subject=subject, body=body)


async def notify_host_overdue(
    db: AsyncSession, *, visit: Visit, visitor: Visitor, host: User | None,
) -> bool:
    """1-hour-overdue reminder to the Host (PRD VMS-CO-010).

    The visitor is still checked in past their planned departure. Returns True
    if delivery was attempted (caller persists `visit.overdue_reminder_sent_at`).
    """
    if not host or not host.email:
        return False
    name = f"{visitor.first_name} {visitor.last_name}"
    subject = f"VMS — Visitor overdue: {name} still on-site"
    body = (
        f"A visitor you are hosting is still checked in past their planned "
        f"departure time.\n\n"
        f"Visitor:          {name}\n"
        f"Company:          {visitor.company_name}\n"
        f"Planned departure:{_fmt_dt(visit.planned_departure)}\n"
        f"Checked in at:    {_fmt_dt(visit.actual_arrival)}\n"
        f"Access area:      {visit.access_area.value.replace('_', ' ')}\n"
        f"Visit ID:         {visit.id}\n\n"
        f"Please check the visitor out in VMS once they have left.\n"
    )
    smtp_cfg = await _load_smtp_config(db)
    return await _send_email(cfg=smtp_cfg, to=host.email, subject=subject, body=body)


async def notify_manager_overdue_escalation(
    db: AsyncSession,
    *,
    visit: Visit,
    visitor: Visitor,
    host: User | None,
    manager: User,
) -> bool:
    """4-hour-overdue escalation to the Host's Department Manager (VMS-CO-011).

    Returns True if delivery was attempted (caller persists
    `visit.overdue_escalated_at`).
    """
    if not manager or not manager.email:
        return False
    name = f"{visitor.first_name} {visitor.last_name}"
    subject = f"VMS — Escalation: visitor {name} overdue 4h+"
    body = (
        f"A visitor has been on-site more than four hours past their planned "
        f"departure and has not been checked out.\n\n"
        f"Visitor:          {name}\n"
        f"Company:          {visitor.company_name}\n"
        f"Host:             {host.full_name if host else '—'}\n"
        f"Planned departure:{_fmt_dt(visit.planned_departure)}\n"
        f"Checked in at:    {_fmt_dt(visit.actual_arrival)}\n"
        f"Access area:      {visit.access_area.value.replace('_', ' ')}\n"
        f"Visit ID:         {visit.id}\n\n"
        f"Please follow up with the Host to confirm the visitor has left and "
        f"check them out in VMS.\n"
    )
    smtp_cfg = await _load_smtp_config(db)
    return await _send_email(cfg=smtp_cfg, to=manager.email, subject=subject, body=body)


async def maybe_dispatch_visit_notifications(
    db: AsyncSession,
    *,
    visit: Visit,
    visitor: Visitor,
    host: User | None,
) -> list[str]:
    """Send training email per the access-area rules. PPE notification has
    moved to a Host-driven opt-in (see `notify_janitor_ppe_request`) — this
    function only handles the training heads-up now.

    Returns the list of channels actually dispatched (`"training"`) for the
    audit log to record.
    """
    if visit.access_area not in _NEEDS_TRAINING_AND_PPE:
        return []

    cfg = (await db.execute(
        text("SELECT notification_contacts FROM vms_config LIMIT 1")
    )).scalar_one_or_none()
    contacts: dict = cfg if isinstance(cfg, dict) else {}

    training_email = contacts.get("training_email")
    if not training_email:
        log.info(
            "Visit %s in %s requires training but no training contact is configured",
            visit.id, visit.access_area.value,
        )
        return []

    smtp_cfg = await _load_smtp_config(db)
    subj, body = _format_training_email(visit, visitor, host)
    if await _send_email(cfg=smtp_cfg, to=training_email, subject=subj, body=body):
        return ["training"]
    return ["training[skipped]"]
