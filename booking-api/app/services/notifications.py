"""iMIP email notification service for meeting-room bookings.

Replaces the Task-10 stub with real email + calendar-invite delivery.

Delivery model:
  enqueue()          — resolves recipients, inserts a pending NotificationLog,
                       then attempts immediate send_notification().
  send_notification() — builds + sends the multipart/mixed iMIP email;
                        updates log + booking.sync_status on success/failure.
                        In LOG-ONLY mode (no SMTP host) marks sent with a marker.

The scheduler (scheduler.py) picks up failed logs and retries with exponential
backoff (5 min * 2^retry_count).  After 5 failures a sync_alert is inserted.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud.config import get_or_create_config
from app.models.booking import Booking
from app.models.notification import NotificationLog
from app.models.room import MeetingRoom
from app.models.user_mirror import User
from app.services.ical import build_event_ics

log = logging.getLogger(__name__)

_TZ = ZoneInfo(settings.DISPLAY_TIMEZONE)

# ── SMTP config ───────────────────────────────────────────────────────────────


async def _load_smtp_config(db: AsyncSession) -> dict[str, Any]:
    """Resolve SMTP credentials for outbound booking email.

    Lookup order:
      1. booking_config.smtp_settings JSONB (Booking Admin panel — preferred).
      2. public.company_config shared SMTP columns (EPMS legacy fallback).

    Returns dict with keys: host, port, user, password, use_tls, from_email.
    Missing fields are None; caller skips delivery when host is missing.
    """
    booking_row = (
        await db.execute(text("SELECT smtp_settings FROM booking_config LIMIT 1"))
    ).scalar_one_or_none()
    bk_cfg: dict = booking_row if isinstance(booking_row, dict) else {}
    if bk_cfg.get("host"):
        return {
            "host":       bk_cfg.get("host"),
            "port":       bk_cfg.get("port"),
            "user":       bk_cfg.get("user"),
            "password":   bk_cfg.get("password"),
            "use_tls":    bk_cfg.get("use_tls"),
            "from_email": bk_cfg.get("from_email"),
        }

    # Fallback: shared epms-api SMTP columns (may not exist in test/stripped DB).
    # Use a SAVEPOINT so a missing-column error doesn't abort the outer transaction.
    try:
        await db.execute(text("SAVEPOINT smtp_fallback"))
        row = (
            await db.execute(
                text(
                    "SELECT smtp_host, smtp_port, smtp_user, smtp_password, "
                    "smtp_use_tls, smtp_from FROM company_config LIMIT 1"
                )
            )
        ).first()
        await db.execute(text("RELEASE SAVEPOINT smtp_fallback"))
    except Exception:  # noqa: BLE001
        try:
            await db.execute(text("ROLLBACK TO SAVEPOINT smtp_fallback"))
        except Exception:  # noqa: BLE001
            pass
        return {}
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


# ── Email builder ─────────────────────────────────────────────────────────────

def _build_plain_text(
    booking: Booking,
    room: MeetingRoom,
    organizer_name: str,
    attendee_names: list[str],
    notif_type: str,
) -> str:
    """Build the human-readable plain-text summary."""
    tz = _TZ
    starts_local = booking.starts_at.astimezone(tz)
    ends_local = booking.ends_at.astimezone(tz)
    time_range = (
        f"{starts_local.strftime('%Y-%m-%d %H:%M')} — "
        f"{ends_local.strftime('%H:%M')} ({settings.DISPLAY_TIMEZONE})"
    )
    location_parts = [p for p in [room.building, room.floor, room.area] if p]
    location = f"{room.name} ({', '.join(location_parts)})" if location_parts else room.name

    action_map = {
        "created": "A meeting room has been booked for you.",
        "updated": "Your meeting room booking has been updated.",
        "cancelled": "Your meeting room booking has been cancelled.",
    }
    action_line = action_map.get(notif_type, f"Notification type: {notif_type}")

    attendees_str = ", ".join(attendee_names) if attendee_names else "(none)"
    return (
        f"{action_line}\n\n"
        f"Title:      {booking.title}\n"
        f"When:       {time_range}\n"
        f"Room:       {location}\n"
        f"Organizer:  {organizer_name}\n"
        f"Attendees:  {attendees_str}\n"
        f"\nBooking ID: {booking.id}\n"
    )


def _build_email_message(
    *,
    from_email: str,
    to_emails: list[str],
    subject: str,
    plain_text: str,
    ics_bytes: bytes,
    ical_method: str,
) -> MIMEMultipart:
    """Construct the multipart/mixed message with:
      - multipart/alternative (text/plain + text/calendar)
      - application/ics attachment (invite.ics)
    """
    msg = MIMEMultipart("mixed")
    msg["From"] = from_email
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject

    # Inner alternative: plain text + calendar
    alternative = MIMEMultipart("alternative")

    text_part = MIMEText(plain_text, "plain", "utf-8")
    alternative.attach(text_part)

    cal_part = MIMEText(ics_bytes.decode("utf-8"), "calendar", "utf-8")
    cal_part.replace_header("Content-Type", f'text/calendar; method={ical_method}; charset=utf-8')
    alternative.attach(cal_part)

    msg.attach(alternative)

    # ICS attachment
    ics_attachment = MIMEBase("application", "ics")
    ics_attachment.set_payload(ics_bytes)
    encoders.encode_base64(ics_attachment)
    ics_attachment.add_header(
        "Content-Disposition", "attachment", filename="invite.ics"
    )
    ics_attachment.add_header("Content-Description", "invite.ics")
    msg.attach(ics_attachment)

    return msg


# ── SMTP send (sync, run in thread) ──────────────────────────────────────────

def _send_smtp_blocking(*, cfg: dict[str, Any], msg: MIMEMultipart) -> None:
    host = cfg["host"]
    port = int(cfg.get("port") or 25)
    use_tls = bool(cfg.get("use_tls"))

    with smtplib.SMTP(host, port, timeout=10) as s:
        if use_tls:
            s.starttls()
        if cfg.get("user"):
            s.login(cfg["user"], cfg.get("password") or "")
        s.send_message(msg)


# ── Core send function ────────────────────────────────────────────────────────

async def send_notification(db: AsyncSession, log_entry: NotificationLog) -> bool:
    """Load booking + room, build iMIP email, deliver via SMTP.

    Updates NotificationLog and Booking.sync_status in place (no commit —
    caller commits or the session closes).

    Returns True on success, False on failure.
    """
    try:
        # Load booking
        booking_result = await db.execute(
            select(Booking).where(Booking.id == log_entry.booking_id)
        )
        booking = booking_result.scalar_one_or_none()
        if booking is None:
            log.error("send_notification: booking %s not found", log_entry.booking_id)
            log_entry.status = "failed"
            log_entry.error = f"booking {log_entry.booking_id} not found"
            return False

        # Load room
        room_result = await db.execute(
            select(MeetingRoom).where(MeetingRoom.id == booking.room_id)
        )
        room = room_result.scalar_one_or_none()
        if room is None:
            log.error("send_notification: room %s not found", booking.room_id)
            log_entry.status = "failed"
            log_entry.error = f"room {booking.room_id} not found"
            booking.sync_status = "failed"
            return False

        # Load config to determine organizer_mode
        config = await get_or_create_config(db)
        smtp_cfg = await _load_smtp_config(db)

        # Resolve organizer
        if config.organizer_mode == "initiator":
            org_result = await db.execute(
                select(User).where(User.id == booking.organizer_id)
            )
            org_user = org_result.scalar_one_or_none()
            organizer_email = org_user.email if org_user else (smtp_cfg.get("from_email") or "booking@system")
            organizer_cn = org_user.full_name if org_user else None
            organizer_name = org_user.full_name if org_user else "Unknown"
        else:
            # system mode
            from_email_val = smtp_cfg.get("from_email") or "booking@system"
            organizer_email = from_email_val
            organizer_cn = "UniOps Booking"
            organizer_name = "UniOps Booking"

        from_email = smtp_cfg.get("from_email") or organizer_email

        # Build subject
        tz = _TZ
        starts_local = booking.starts_at.astimezone(tz)
        type_label = {"created": "Created", "updated": "Updated", "cancelled": "Cancelled"}.get(
            log_entry.notif_type, log_entry.notif_type.title()
        )
        subject = (
            f"[Booking] {type_label}: {booking.title} "
            f"— {room.name} {starts_local.strftime('%Y-%m-%d %H:%M')}"
        )

        # iCAL method
        ical_method = "CANCEL" if log_entry.notif_type == "cancelled" else "REQUEST"

        # Recipient list from log (already resolved by enqueue)
        to_emails: list[str] = list(log_entry.recipients or [])
        if not to_emails:
            log.warning("send_notification: no recipients on log %s", log_entry.id)
            log_entry.status = "failed"
            log_entry.error = "no recipients"
            booking.sync_status = "failed"
            return False

        # Attendee emails for the iCAL ATTENDEE lines (organizer excluded from attendees)
        attendee_emails = [e for e in to_emails if e != organizer_email]

        # Pull attendee names for plain text (best-effort, no N+1 guard needed for small lists)
        attendee_name_rows = await db.execute(
            select(User.full_name).where(User.email.in_(attendee_emails))
        )
        attendee_names = [r[0] for r in attendee_name_rows.all()]

        # Build ICS
        # rrule: use log's booking rrule field (series bookings carry it)
        ics_bytes = build_event_ics(
            booking=booking,
            room=room,
            organizer_email=organizer_email,
            attendee_emails=attendee_emails,
            method=ical_method,
            rrule=booking.rrule,
            organizer_cn=organizer_cn,
        )

        # Build plain text
        plain_text = _build_plain_text(booking, room, organizer_name, attendee_names, log_entry.notif_type)

        # Build message
        email_msg = _build_email_message(
            from_email=from_email,
            to_emails=to_emails,
            subject=subject,
            plain_text=plain_text,
            ics_bytes=ics_bytes,
            ical_method=ical_method,
        )

        # LOG-ONLY mode (no SMTP host configured)
        if not smtp_cfg.get("host"):
            log.info(
                "SMTP not configured — log-only notification for booking %s "
                "(type=%s, to=%s, subject=%r)",
                booking.id, log_entry.notif_type, to_emails, subject,
            )
            log_entry.status = "sent"
            log_entry.sent_at = datetime.now(timezone.utc)
            log_entry.error = "smtp_not_configured (logged only)"
            booking.sync_status = "sent"
            return True

        # Deliver via SMTP (blocking call in thread)
        await asyncio.to_thread(_send_smtp_blocking, cfg=smtp_cfg, msg=email_msg)

        log_entry.status = "sent"
        log_entry.sent_at = datetime.now(timezone.utc)
        booking.sync_status = "sent"
        log.info(
            "Notification sent for booking %s (type=%s, to=%s)",
            booking.id, log_entry.notif_type, to_emails,
        )
        return True

    except Exception as exc:  # noqa: BLE001
        log.exception(
            "send_notification failed for log %s: %s", log_entry.id, exc
        )
        log_entry.status = "failed"
        log_entry.error = str(exc)
        try:
            # Best-effort sync_status update
            booking_result2 = await db.execute(
                select(Booking).where(Booking.id == log_entry.booking_id)
            )
            bk = booking_result2.scalar_one_or_none()
            if bk is not None:
                bk.sync_status = "failed"
        except Exception:  # noqa: BLE001
            pass
        return False


# ── enqueue — public entry point ─────────────────────────────────────────────

async def enqueue(
    db: AsyncSession,
    bookings: list[Booking],
    notif_type: str,
    *,
    rrule: str | None = None,
) -> NotificationLog | None:
    """Resolve recipients, insert a pending NotificationLog, attempt immediate send.

    For series bookings, pass the first occurrence + rrule; a single notification
    email covers the whole series (one RRULE VEVENT).

    Returns the NotificationLog (status=sent|failed) or None on unexpected error.
    Never raises — all exceptions are caught internally.
    """
    try:
        if not bookings:
            return None

        booking = bookings[0]  # use first occurrence as the envelope booking

        config = await get_or_create_config(db)
        rules = config.rules or {}

        # ── Resolve recipients ────────────────────────────────────────────────
        recipient_emails: list[str] = []
        skipped_names: list[str] = []

        # Organizer
        org_result = await db.execute(
            select(User).where(User.id == booking.organizer_id)
        )
        org_user = org_result.scalar_one_or_none()
        if org_user:
            if org_user.email and org_user.email.strip():
                recipient_emails.append(org_user.email)
            else:
                skipped_names.append(org_user.full_name)

        # Attendees
        attendee_id_list: list[uuid.UUID] = []
        for aid in (booking.attendee_ids or []):
            try:
                attendee_id_list.append(uuid.UUID(str(aid)))
            except (ValueError, TypeError):
                continue

        if attendee_id_list:
            att_result = await db.execute(
                select(User).where(User.id.in_(attendee_id_list))
            )
            att_users = att_result.scalars().all()
            for att in att_users:
                if att.email and att.email.strip():
                    if att.email not in recipient_emails:
                        recipient_emails.append(att.email)
                else:
                    skipped_names.append(att.full_name)

        # Room admin emails (if enabled)
        if rules.get("notify_room_admin") and rules.get("room_admin_emails"):
            for admin_email in rules["room_admin_emails"]:
                if admin_email and admin_email not in recipient_emails:
                    recipient_emails.append(admin_email)

        # Build skip note
        skip_note: str | None = None
        if skipped_names:
            skip_note = "skipped_no_email: " + ", ".join(skipped_names)
            log.info("enqueue: %s", skip_note)

        # ── Insert NotificationLog (pending) ──────────────────────────────────
        log_entry = NotificationLog(
            booking_id=booking.id,
            notif_type=notif_type,
            recipients=recipient_emails,
            status="pending",
            error=skip_note,
            retry_count=0,
        )
        db.add(log_entry)
        await db.flush()
        await db.refresh(log_entry)

        # ── Attempt immediate send ────────────────────────────────────────────
        await send_notification(db, log_entry)

        return log_entry

    except Exception as exc:  # noqa: BLE001
        log.exception("enqueue failed unexpectedly: %s", exc)
        return None
