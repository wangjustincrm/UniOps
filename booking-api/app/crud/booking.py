"""CRUD helpers for Booking records.

create_booking_records: batch-insert bookings for a single or series request,
write one audit log row per booking, and return the list of created Booking
ORM instances.

Consumed by:
  - app/api/v1/bookings.py (Task 7 create flow)
  - Task 8 (modify/rebook flow)
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import BookingAuditLog
from app.models.booking import Booking
from app.models.room import MeetingRoom
from app.schemas.booking import BookingCreate


async def create_booking_records(
    db: AsyncSession,
    *,
    payload: BookingCreate,
    occurrences: list[tuple[datetime, datetime]],
    organizer_id: uuid.UUID,
    series_id: uuid.UUID | None,
    rrule: str | None,
    calendar_uid: str,
    room: MeetingRoom,
    organizer_name: str,
) -> list[Booking]:
    """Insert one Booking row per occurrence plus one audit row per booking.

    All rows are flushed within the caller's transaction; the caller is
    responsible for committing (or the session autocommits on request end).

    Args:
        db:             AsyncSession.
        payload:        Original BookingCreate request body.
        occurrences:    Expanded list of (starts_at, ends_at) tuples.
        organizer_id:   UUID of the booking organiser (JWT sub).
        series_id:      Shared UUID for series bookings; None for singles.
        rrule:          RRULE string for series; None for singles.
        calendar_uid:   Shared calendar UID (``f"{uuid4()}@uniops"``).
        room:           MeetingRoom ORM instance (for FK and summary snapshot).
        organizer_name: Full name of the organizer (for audit snapshot).

    Returns:
        List of created Booking ORM instances (same order as occurrences).
    """
    bookings: list[Booking] = []

    for starts_at, ends_at in occurrences:
        booking = Booking(
            room_id=room.id,
            title=payload.title,
            description=payload.description,
            organizer_id=organizer_id,
            attendee_ids=[str(aid) for aid in (payload.attendee_ids or [])],
            starts_at=starts_at,
            ends_at=ends_at,
            status="confirmed",
            series_id=series_id,
            rrule=rrule,
            calendar_uid=calendar_uid,
            ical_sequence=0,
            sync_status="pending",
        )
        db.add(booking)
        bookings.append(booking)

    # Flush to get IDs before writing audit rows
    await db.flush()

    for booking in bookings:
        # Snapshot of the created booking state (serialise datetimes as ISO strings)
        after_snapshot = {
            "id": str(booking.id),
            "room_id": str(booking.room_id),
            "room_name": room.name,
            "room_code": room.code,
            "title": booking.title,
            "description": booking.description,
            "organizer_id": str(booking.organizer_id),
            "organizer_name": organizer_name,
            "attendee_ids": booking.attendee_ids,
            "starts_at": booking.starts_at.isoformat(),
            "ends_at": booking.ends_at.isoformat(),
            "status": booking.status,
            "series_id": str(booking.series_id) if booking.series_id else None,
            "rrule": booking.rrule,
            "calendar_uid": booking.calendar_uid,
            "sync_status": booking.sync_status,
        }
        audit = BookingAuditLog(
            booking_id=booking.id,
            action="create",
            actor_id=organizer_id,
            before=None,
            after=after_snapshot,
        )
        db.add(audit)

    await db.flush()
    return bookings
