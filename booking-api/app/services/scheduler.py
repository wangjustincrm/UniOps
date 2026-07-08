"""In-process background scheduler for booking-api.

Mirrors the vms-api scheduler shape (asyncio task, Postgres advisory lock,
interval from settings).

Each tick:
  1. Take a Postgres session-level advisory lock (so multi-replica deploys
     don't double-fire).
  2. Pick NotificationLog rows where status='failed' AND notif_type != 'sync_alert'
     AND retry_count < 5.
  3. For each eligible row: apply exponential backoff gate
     (next_try = (sent_at or created_at) + 5min * 2^retry_count).
  4. Set booking.sync_status = 'compensating'.
  5. Increment retry_count.
  6. Call send_notification().
  7. If this was the 5th attempt (new retry_count == 5) and it still failed,
     insert a sync_alert NotificationLog to rules.room_admin_emails and
     attempt one immediate send (sync_alert rows are NOT retried by the
     scheduler — they are filtered out by notif_type != 'sync_alert').

Advisory lock key:  0x424F4F4B_5343_4844  ("BOOKSCHED")
— distinct from the VMS key (0x564D5343_4845_4400).

Tests invoke `_run_tick_with_session(db)` directly with SCHEDULER_ENABLED=false
so the asyncio loop is never started.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.notification import NotificationLog

log = logging.getLogger(__name__)

# Postgres advisory lock key — "BOOKSCHED" in hex, distinct from VMS.
_ADVISORY_LOCK_KEY = 0x424F4F4B53434844  # "BOOKSCHED"

# Backoff base: 5 minutes
_BACKOFF_BASE_SECONDS = 300


async def _run_tick_with_session(db: AsyncSession) -> None:
    """One retry-scheduler tick, using the supplied session.

    Called directly in tests (no advisory lock, no new session).
    Called by _run_tick() in production (inside a scoped session).
    """
    from app.crud.config import get_or_create_config
    from app.models.booking import Booking
    from app.services.notifications import send_notification

    now = datetime.now(timezone.utc)

    # Pick failed notification logs (not sync_alerts; retry_count < 5)
    result = await db.execute(
        select(NotificationLog).where(
            NotificationLog.status == "failed",
            NotificationLog.notif_type != "sync_alert",
            NotificationLog.retry_count < 5,
        )
    )
    failed_logs = result.scalars().all()

    for log_entry in failed_logs:
        # Backoff gate: only retry if enough time has elapsed
        base_time = log_entry.sent_at or log_entry.created_at
        if base_time is not None:
            # Ensure timezone-aware
            if base_time.tzinfo is None:
                base_time = base_time.replace(tzinfo=timezone.utc)
            wait_seconds = _BACKOFF_BASE_SECONDS * (2 ** log_entry.retry_count)
            next_try = base_time + timedelta(seconds=wait_seconds)
            if now < next_try:
                log.debug(
                    "Scheduler: skipping log %s (retry_count=%d, next_try=%s)",
                    log_entry.id, log_entry.retry_count, next_try.isoformat(),
                )
                continue

        # Mark compensating on the booking
        if log_entry.booking_id is not None:
            bk_result = await db.execute(
                select(Booking).where(Booking.id == log_entry.booking_id)
            )
            booking = bk_result.scalar_one_or_none()
            if booking is not None:
                booking.sync_status = "compensating"

        # Increment retry count before attempt
        log_entry.retry_count += 1
        await db.flush()

        # Attempt delivery
        success = await send_notification(db, log_entry)
        await db.flush()

        if not success and log_entry.retry_count >= 5:
            # Final failure — insert sync_alert
            log.warning(
                "Scheduler: log %s exhausted retries (booking_id=%s). "
                "Inserting sync_alert.",
                log_entry.id, log_entry.booking_id,
            )
            try:
                config = await get_or_create_config(db)
                rules = config.rules or {}
                admin_emails: list[str] = rules.get("room_admin_emails") or []

                alert_recipients = admin_emails or []
                alert = NotificationLog(
                    booking_id=log_entry.booking_id,
                    notif_type="sync_alert",
                    recipients=alert_recipients,
                    status="pending",
                    error=(
                        f"Sync alert: notification log {log_entry.id} "
                        f"failed after 5 retries. Last error: {log_entry.error}"
                    ),
                    retry_count=5,  # start at 5 so scheduler picker skips it
                )
                db.add(alert)
                await db.flush()

                # Attempt one immediate send of the alert (best-effort)
                if alert_recipients:
                    await send_notification(db, alert)
                    await db.flush()
                else:
                    alert.status = "sent"
                    alert.error = "sync_alert: no admin emails configured"
                    await db.flush()

            except Exception:  # noqa: BLE001
                log.exception("Scheduler: failed to insert/send sync_alert for log %s", log_entry.id)


async def _run_tick() -> None:
    """One scheduler iteration with its own session + advisory lock."""
    async with AsyncSessionLocal() as db:
        got = (await db.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY}
        )).scalar_one()
        if not got:
            log.debug("Booking scheduler tick skipped — advisory lock held elsewhere")
            return
        try:
            await _run_tick_with_session(db)
            await db.commit()
        except Exception:
            await db.rollback()
            log.exception("Booking scheduler tick failed")
        finally:
            await db.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY}
            )
            await db.commit()


async def _loop() -> None:
    interval = max(60, settings.SCHEDULER_INTERVAL_SECONDS)
    log.info("Booking scheduler started (interval=%ss)", interval)
    try:
        while True:
            await _run_tick()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("Booking scheduler stopping")
        raise


def start(app) -> asyncio.Task | None:  # noqa: ARG001
    """Create the background scheduler task (call from lifespan startup).

    Returns the asyncio.Task so the caller can cancel it on shutdown,
    or None when SCHEDULER_ENABLED=false.
    """
    if not settings.SCHEDULER_ENABLED:
        log.info("Booking scheduler disabled (SCHEDULER_ENABLED=false)")
        return None
    return asyncio.create_task(_loop(), name="booking-scheduler")


async def stop(task: asyncio.Task | None) -> None:
    """Cancel + await the scheduler task (call from lifespan shutdown)."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
