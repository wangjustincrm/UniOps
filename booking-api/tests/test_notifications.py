"""TDD tests for Task 10: iMIP email notifications, retry scheduler, admin monitor.

Step 1 — RED: All tests written before any implementation. They must FAIL first.

SMTP is monkeypatched with a recorder so tests never hit real mail servers.
DB interactions use the shared db_session fixture (savepoint-isolated, rolled back).

Coverage:
  1. created enqueue: one email; To covers organizer + 2 attendees; text/calendar
     part contains METHOD:REQUEST and BEGIN:VCALENDAR; invite.ics attachment present;
     log.status=sent; booking.sync_status=sent.
  2. attendee without email: skipped, others still sent, log.error mentions name.
  3. SMTP raising in send_message: log.status=failed + error captured +
     booking.sync_status=failed; enqueue does NOT raise.
  4. resend endpoint: failed log → SMTP succeeds → 200 + status=sent.
  5. smtp unconfigured (empty smtp_settings + no company_config SMTP): log-only →
     status=sent + error contains "smtp_not_configured".
  6. cancelled notif: METHOD:CANCEL in text/calendar part.
  7. scheduler tick unit test (two sub-cases):
     a. seed failed log + old created_at, SMTP patched success → after tick: sent.
     b. seed retry_count=4 + SMTP failing → after tick: retry_count=5, sync_alert log exists.
"""
from __future__ import annotations

import email as stdlib_email
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from tests.conftest import make_token, authed_client, make_user

TZ = ZoneInfo("America/Toronto")


# ─────────────────────────────────────────────────────────────────────────────
# SMTP recorder (replaces smtplib.SMTP)
# ─────────────────────────────────────────────────────────────────────────────

class _SMTPRecorder:
    """Minimal smtplib.SMTP stand-in that records calls for assertions.

    Attributes set after context-manager exit:
      host, port       — from __init__
      logged_in        — (user, password) tuple or None
      sent_messages    — list of email.message.Message objects passed to send_message
    """
    instances: list["_SMTPRecorder"] = []  # class-level registry for easy inspection

    def __init__(self, host: str, port: int, timeout: int = 10):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logged_in: tuple[str, str] | None = None
        self.sent_messages: list[Any] = []
        self._starttls_called = False
        _SMTPRecorder.instances.append(self)

    def starttls(self):
        self._starttls_called = True

    def login(self, user: str, password: str):
        self.logged_in = (user, password)

    def send_message(self, msg):
        self.sent_messages.append(msg)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class _SMTPFailRecorder(_SMTPRecorder):
    """Same as recorder but send_message always raises."""

    def send_message(self, msg):
        raise ConnectionRefusedError("test SMTP failure")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _dt(h: int, days_ahead: int = 1) -> datetime:
    """TZ-aware datetime in America/Toronto."""
    d = datetime.now(TZ).date() + timedelta(days=days_ahead)
    return datetime(d.year, d.month, d.day, h, 0, tzinfo=TZ)


async def _make_room(db_session):
    """Insert a MeetingRoom row and return the ORM instance."""
    from app.models.room import MeetingRoom
    room = MeetingRoom(
        name="Test Room",
        code=f"TR-{uuid.uuid4().hex[:6]}",
        campus="Main",
        building="HQ",
        floor="1",
        capacity=10,
        equipment=[],
        room_type="standard",
    )
    db_session.add(room)
    await db_session.flush()
    return room


async def _make_booking(db_session, room_id, organizer_id, attendee_ids=None):
    """Insert a confirmed Booking row and return it."""
    from app.models.booking import Booking
    booking = Booking(
        room_id=room_id,
        title="Test Meeting",
        organizer_id=organizer_id,
        attendee_ids=[str(aid) for aid in (attendee_ids or [])],
        starts_at=_dt(10),
        ends_at=_dt(11),
        status="confirmed",
        calendar_uid=f"test-{uuid.uuid4()}@booking-test.com",
        ical_sequence=0,
        sync_status="pending",
    )
    db_session.add(booking)
    await db_session.flush()
    return booking


async def _configure_smtp(db_session):
    """Insert a BookingConfig row with a fake SMTP host so notifications fire."""
    from app.crud.config import get_or_create_config
    config = await get_or_create_config(db_session)
    config.smtp_settings = {
        "host": "smtp.test.example.com",
        "port": 587,
        "user": "booking@example.com",
        "password": "secret",
        "use_tls": True,
        "from_email": "booking@example.com",
    }
    config.organizer_mode = "system"
    config.rules = {
        "notify_room_admin": False,
        "room_admin_emails": [],
        "slot_minutes": 15,
        "min_duration_minutes": 15,
        "max_duration_minutes": 240,
        "advance_days": 30,
        "default_open_start": "08:00",
        "default_open_end": "20:00",
    }
    await db_session.flush()
    return config


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — enqueue creates email (happy path)
# ─────────────────────────────────────────────────────────────────────────────

class TestEnqueueCreated:
    async def test_one_email_sent_to_organizer_and_attendees(
        self, test_engine, db_session, monkeypatch
    ):
        """enqueue('created') with 2 attendees → exactly 1 SMTP send_message call;
        To header covers organizer + 2 attendees."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="organizer@test.com", full_name="Alice Org")
        att1 = await make_user(test_engine, role="requester",
                               email="att1@test.com", full_name="Bob Att")
        att2 = await make_user(test_engine, role="requester",
                               email="att2@test.com", full_name="Carol Att")

        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id,
                                      attendee_ids=[att1.id, att2.id])
        await _configure_smtp(db_session)

        from app.services.notifications import enqueue
        log = await enqueue(db_session, [booking], "created")

        assert log is not None, "enqueue must return a NotificationLog"
        assert log.status == "sent", f"Expected status=sent, got {log.status}"
        assert booking.sync_status == "sent", f"Expected sync_status=sent, got {booking.sync_status}"

        # Exactly one SMTP instance (one call)
        assert len(_SMTPRecorder.instances) == 1
        recorder = _SMTPRecorder.instances[0]
        assert len(recorder.sent_messages) == 1

        msg = recorder.sent_messages[0]
        to_addrs = msg["To"]
        assert "organizer@test.com" in to_addrs
        assert "att1@test.com" in to_addrs
        assert "att2@test.com" in to_addrs

    async def test_email_contains_text_calendar_with_method_request(
        self, test_engine, db_session, monkeypatch
    ):
        """The sent email must contain a text/calendar MIME part with METHOD:REQUEST."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="org2@test.com", full_name="Org Two")
        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id)
        await _configure_smtp(db_session)

        from app.services.notifications import enqueue
        await enqueue(db_session, [booking], "created")

        recorder = _SMTPRecorder.instances[0]
        msg = recorder.sent_messages[0]

        # Inspect MIME parts for text/calendar
        cal_parts = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/calendar":
                    cal_parts.append(part)

        assert cal_parts, "No text/calendar MIME part found in email"
        cal_payload = cal_parts[0].get_payload(decode=True)
        if isinstance(cal_payload, bytes):
            cal_text = cal_payload.decode("utf-8", errors="replace")
        else:
            cal_text = str(cal_payload)
        assert "METHOD:REQUEST" in cal_text, f"text/calendar missing METHOD:REQUEST: {cal_text[:300]}"
        assert "BEGIN:VCALENDAR" in cal_text

    async def test_email_has_ics_attachment(
        self, test_engine, db_session, monkeypatch
    ):
        """The sent email must also have an application/ics attachment named invite.ics."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="org3@test.com", full_name="Org Three")
        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id)
        await _configure_smtp(db_session)

        from app.services.notifications import enqueue
        await enqueue(db_session, [booking], "created")

        recorder = _SMTPRecorder.instances[0]
        msg = recorder.sent_messages[0]

        ics_attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                cd = part.get("Content-Disposition", "")
                ct = part.get_content_type()
                filename = part.get_filename()
                if filename and filename.lower() == "invite.ics":
                    ics_attachments.append(part)
                elif ct in ("application/ics", "application/octet-stream") and "invite.ics" in ct:
                    ics_attachments.append(part)

        assert ics_attachments, (
            "No invite.ics attachment found in email. "
            f"Parts: {[p.get_content_type() for p in msg.walk()]}"
        )

    async def test_log_status_sent_after_successful_send(
        self, test_engine, db_session, monkeypatch
    ):
        """NotificationLog.status must be 'sent' after successful SMTP delivery."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="org4@test.com", full_name="Org Four")
        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id)
        await _configure_smtp(db_session)

        from app.services.notifications import enqueue
        log = await enqueue(db_session, [booking], "created")

        assert log is not None
        assert log.status == "sent"
        assert log.sent_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — attendee without email skipped
# ─────────────────────────────────────────────────────────────────────────────

class TestAttendeeSkipped:
    async def test_attendee_without_email_skipped_and_noted(
        self, test_engine, db_session, monkeypatch
    ):
        """An attendee whose email is empty/None must be skipped; others still sent.
        The log.error must mention the skipped user's name."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="org5@test.com", full_name="Org Five")
        att_ok = await make_user(test_engine, role="requester",
                                 email="att_ok@test.com", full_name="Good Att")
        # Attendee with no email — insert directly to bypass any NOT NULL on email
        from app.models.user_mirror import User
        no_email_user = User(
            id=uuid.uuid4(),
            full_name="No Email Person",
            email="",  # empty string
            role="requester",
            is_active=True,
        )
        db_session.add(no_email_user)
        await db_session.flush()

        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id,
                                      attendee_ids=[att_ok.id, no_email_user.id])
        await _configure_smtp(db_session)

        from app.services.notifications import enqueue
        log = await enqueue(db_session, [booking], "created")

        assert log is not None
        # Delivery still succeeds (to the users that have email)
        assert log.status == "sent", f"Expected sent, got {log.status}: {log.error}"
        # log.error mentions the skipped user
        assert log.error is not None
        assert "No Email Person" in log.error, (
            f"Expected skipped name in log.error, got: {log.error}"
        )
        # The email is still sent (to organizer + att_ok)
        assert len(_SMTPRecorder.instances) == 1
        recorder = _SMTPRecorder.instances[0]
        assert len(recorder.sent_messages) == 1
        msg = recorder.sent_messages[0]
        assert "att_ok@test.com" in msg["To"]
        assert "org5@test.com" in msg["To"]


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — SMTP failure path
# ─────────────────────────────────────────────────────────────────────────────

class TestSMTPFailure:
    async def test_smtp_exception_marks_log_failed_and_booking_sync_failed(
        self, test_engine, db_session, monkeypatch
    ):
        """If SMTP.send_message raises, log.status=failed, booking.sync_status=failed,
        and enqueue does NOT raise (caller isolation)."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPFailRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="org6@test.com", full_name="Org Six")
        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id)
        await _configure_smtp(db_session)

        from app.services.notifications import enqueue
        # Must not raise
        log = await enqueue(db_session, [booking], "created")

        assert log is not None
        assert log.status == "failed", f"Expected failed, got {log.status}"
        assert log.error is not None and len(log.error) > 0
        assert booking.sync_status == "failed"

    async def test_enqueue_does_not_raise_on_smtp_error(
        self, test_engine, db_session, monkeypatch
    ):
        """enqueue must never propagate exceptions from send_notification."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPFailRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="org7@test.com", full_name="Org Seven")
        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id)
        await _configure_smtp(db_session)

        from app.services.notifications import enqueue
        # This must not raise
        try:
            await enqueue(db_session, [booking], "created")
        except Exception as exc:
            pytest.fail(f"enqueue raised unexpectedly: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — SMTP unconfigured → log-only mode
# ─────────────────────────────────────────────────────────────────────────────

class TestSMTPUnconfigured:
    async def test_no_smtp_config_logs_only_marks_sent(
        self, test_engine, db_session, monkeypatch
    ):
        """When smtp_settings is empty and company_config has no SMTP host,
        the notification is log-only: status=sent, error contains 'smtp_not_configured'."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="org8@test.com", full_name="Org Eight")
        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id)

        # Ensure no SMTP config at all (get_or_create gives empty smtp_settings by default)
        from app.crud.config import get_or_create_config
        config = await get_or_create_config(db_session)
        config.smtp_settings = {}
        await db_session.flush()

        from app.services.notifications import enqueue
        log = await enqueue(db_session, [booking], "created")

        assert log is not None
        # No SMTP calls — recorder should be empty
        assert len(_SMTPRecorder.instances) == 0, "SMTP must not be called in log-only mode"
        assert log.status == "sent", f"Expected sent (log-only), got {log.status}"
        assert log.error is not None
        assert "smtp_not_configured" in log.error.lower() or "smtp_not_configured" in log.error


# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — cancelled notification → METHOD:CANCEL
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelledNotification:
    async def test_cancelled_notif_uses_method_cancel(
        self, test_engine, db_session, monkeypatch
    ):
        """enqueue with notif_type='cancelled' must produce METHOD:CANCEL in the
        text/calendar MIME part."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="org9@test.com", full_name="Org Nine")
        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id)
        await _configure_smtp(db_session)

        from app.services.notifications import enqueue
        log = await enqueue(db_session, [booking], "cancelled")

        assert log is not None
        assert len(_SMTPRecorder.instances) == 1
        recorder = _SMTPRecorder.instances[0]
        assert len(recorder.sent_messages) == 1

        msg = recorder.sent_messages[0]
        cal_parts = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/calendar":
                    cal_parts.append(part)

        assert cal_parts, "No text/calendar part in cancelled email"
        cal_payload = cal_parts[0].get_payload(decode=True)
        cal_text = cal_payload.decode("utf-8", errors="replace") if isinstance(cal_payload, bytes) else str(cal_payload)
        assert "METHOD:CANCEL" in cal_text, f"Expected METHOD:CANCEL, got: {cal_text[:300]}"


# ─────────────────────────────────────────────────────────────────────────────
# Section 6 — Admin resend endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminResend:
    async def test_resend_failed_log_marks_sent(
        self, test_engine, db_session, monkeypatch
    ):
        """POST /admin/notifications/{id}/resend on a failed log → 200, status=sent."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="org10@test.com", full_name="Org Ten")
        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id)
        await _configure_smtp(db_session)

        # Create a failed NotificationLog manually
        from app.models.notification import NotificationLog
        log = NotificationLog(
            booking_id=booking.id,
            notif_type="created",
            recipients=["org10@test.com"],
            status="failed",
            error="prior failure",
            retry_count=1,
        )
        db_session.add(log)
        await db_session.flush()

        admin_user = await make_user(test_engine, role="system_admin",
                                     email="admin10@test.com", full_name="Admin Ten")
        token = make_token(admin_user.id, "system_admin")
        async with authed_client(token, session=db_session) as c:
            resp = await c.post(f"/api/v1/admin/notifications/{log.id}/resend")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["status"] == "sent", f"Expected status=sent, got {data['status']}"

    async def test_admin_notifications_list_returns_items(
        self, test_engine, db_session, monkeypatch
    ):
        """GET /admin/notifications returns {items, total} shape."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="org11@test.com", full_name="Org Eleven")
        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id)
        await _configure_smtp(db_session)

        from app.services.notifications import enqueue
        await enqueue(db_session, [booking], "created")

        admin_user = await make_user(test_engine, role="system_admin",
                                     email="admin11@test.com", full_name="Admin Eleven")
        token = make_token(admin_user.id, "system_admin")
        async with authed_client(token, session=db_session) as c:
            resp = await c.get("/api/v1/admin/notifications")

        assert resp.status_code == 200, f"Expected 200: {resp.text}"
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)


# ─────────────────────────────────────────────────────────────────────────────
# Section 7 — Scheduler tick unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSchedulerTick:
    async def test_scheduler_tick_retries_failed_log_and_marks_sent(
        self, test_engine, db_session, monkeypatch
    ):
        """One tick with a failed log (retry_count=0, old created_at) and SMTP patched
        to succeed → log.status=sent after tick."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="org12@test.com", full_name="Org Twelve")
        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id)
        await _configure_smtp(db_session)

        # Insert a failed log with old created_at so backoff is satisfied
        from app.models.notification import NotificationLog
        old_time = datetime.now(timezone.utc) - timedelta(hours=1)
        log = NotificationLog(
            booking_id=booking.id,
            notif_type="created",
            recipients=["org12@test.com"],
            status="failed",
            error="prior smtp error",
            retry_count=0,
        )
        db_session.add(log)
        await db_session.flush()
        # Override created_at via direct attribute (session is not committed yet)
        # We use a raw UPDATE so the server default doesn't override us
        from sqlalchemy import text
        await db_session.execute(
            text("UPDATE booking_notification_log SET created_at = :t WHERE id = :id"),
            {"t": old_time, "id": log.id},
        )
        await db_session.flush()
        # Refresh so ORM object reflects the DB-side created_at we just wrote
        await db_session.refresh(log)

        from app.services.scheduler import _run_tick_with_session
        await _run_tick_with_session(db_session)

        # Refresh again to get updated state after tick
        await db_session.refresh(log)
        assert log.status == "sent", f"Expected sent after tick, got {log.status}"

    async def test_scheduler_tick_5th_failure_creates_sync_alert(
        self, test_engine, db_session, monkeypatch
    ):
        """After 5th failure (retry_count=4, SMTP fails), retry_count becomes 5
        and a sync_alert NotificationLog is inserted."""
        import smtplib
        _SMTPRecorder.instances.clear()
        monkeypatch.setattr(smtplib, "SMTP", _SMTPFailRecorder)

        organizer = await make_user(test_engine, role="requester",
                                    email="org13@test.com", full_name="Org Thirteen")
        room = await _make_room(db_session)
        booking = await _make_booking(db_session, room.id, organizer.id)

        # Configure room admin emails so sync_alert has recipients
        from app.crud.config import get_or_create_config
        config = await get_or_create_config(db_session)
        config.smtp_settings = {
            "host": "smtp.test.example.com",
            "port": 587,
            "user": "booking@example.com",
            "password": "secret",
            "use_tls": True,
            "from_email": "booking@example.com",
        }
        config.organizer_mode = "system"
        config.rules = {
            "notify_room_admin": True,
            "room_admin_emails": ["admin@example.com"],
            "slot_minutes": 15,
            "min_duration_minutes": 15,
            "max_duration_minutes": 240,
            "advance_days": 30,
            "default_open_start": "08:00",
            "default_open_end": "20:00",
        }
        await db_session.flush()

        # Insert a failed log at retry_count=4 with old enough sent_at to pass backoff
        from app.models.notification import NotificationLog
        old_time = datetime.now(timezone.utc) - timedelta(hours=24)
        log = NotificationLog(
            booking_id=booking.id,
            notif_type="created",
            recipients=["org13@test.com"],
            status="failed",
            error="prior smtp error",
            retry_count=4,
        )
        db_session.add(log)
        await db_session.flush()
        from sqlalchemy import text
        await db_session.execute(
            text("UPDATE booking_notification_log SET created_at = :t, sent_at = :t WHERE id = :id"),
            {"t": old_time, "id": log.id},
        )
        await db_session.flush()

        from app.services.scheduler import _run_tick_with_session
        await _run_tick_with_session(db_session)

        await db_session.refresh(log)
        assert log.retry_count == 5, f"Expected retry_count=5, got {log.retry_count}"
        assert log.status == "failed", f"Expected still failed, got {log.status}"

        # A sync_alert row should exist
        from sqlalchemy import select
        result = await db_session.execute(
            select(NotificationLog).where(
                NotificationLog.notif_type == "sync_alert",
                NotificationLog.booking_id == booking.id,
            )
        )
        alert = result.scalar_one_or_none()
        assert alert is not None, "Expected a sync_alert NotificationLog to be created"
