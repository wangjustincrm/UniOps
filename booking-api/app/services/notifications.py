"""Notification stub — Task 10 replaces this with real email/calendar delivery.

The create flow calls enqueue() after a successful booking INSERT so the
import resolves and the call is wired. Task 10 will replace this stub with
real async dispatch logic.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking


async def enqueue(
    db: AsyncSession,
    bookings: list[Booking],
    notif_type: str,
    *,
    rrule: str | None = None,
) -> None:
    """Enqueue a notification for the given bookings.

    Task 10 replaces this stub with real delivery (email + calendar invite).

    Args:
        db:          AsyncSession (passed for future transactional outbox use).
        bookings:    Booking ORM instances that were just created/modified.
        notif_type:  Notification type string, e.g. "created", "cancelled".
        rrule:       RRULE string for series bookings (None for single bookings).
    """
    return None
