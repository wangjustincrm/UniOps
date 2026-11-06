"""GET /admin/bookings and GET /admin/bookings/export endpoints.

Consumed by Tasks 10/14/15/16 for admin oversight of all bookings.

Filter params (overlap on [date_from, date_to]):
    room_id, organizer_id, date_from, date_to, status

Pagination (GET /admin/bookings): limit (default 200, le 500), offset.
Export (GET /admin/bookings/export): no pagination cap — streams all matches as CSV.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, select

from app.core.config import settings
from app.core.deps import SessionDep
from app.core.list_sort import apply_sort, check_sort, label_case
from app.core.permissions import AdminUser
from app.models.booking import Booking
from app.models.room import MeetingRoom
from app.models.user_mirror import User
from app.schemas.booking import AdminBookingListOut, BookingAdminOut

router = APIRouter()

TZ = ZoneInfo(settings.DISPLAY_TIMEZONE)

# All Bookings page headers -> ORDER BY (the ?sort= whitelist). Each sorts by
# what the cell shows: the room NAME, the organizer's NAME (not the FK), the
# status as "No show", the sync label ("Updating" for compensating —
# booking/src/pages/admin/AllBookingsPage.tsx SYNC_LABEL).
_SYNC_LABEL = {"sent": "Sent", "pending": "Pending", "compensating": "Updating",
               "failed": "Failed"}
BOOKING_SORTABLE = {
    "title": func.lower(Booking.title),
    "room": func.lower(MeetingRoom.name),
    "organizer": func.lower(User.full_name),
    "starts_at": Booking.starts_at,
    "status": func.lower(func.replace(Booking.status, "_", " ")),
    "sync_status": label_case(Booking.sync_status, _SYNC_LABEL,
                              else_=func.lower(Booking.sync_status)),
}


def _build_filter_clauses(
    room_id: uuid.UUID | None,
    organizer_id: uuid.UUID | None,
    date_from: date | None,
    date_to: date | None,
    booking_status: str | None,
    organizer: str | None = None,
):
    """Build SQLAlchemy WHERE clauses for admin booking filters.

    `organizer` is a case-insensitive contains-match on the organizer's NAME;
    the statement it is applied to must outer-join User on organizer_id."""
    clauses = []
    if room_id is not None:
        clauses.append(Booking.room_id == room_id)
    if organizer_id is not None:
        clauses.append(Booking.organizer_id == organizer_id)
    if date_from is not None:
        # starts_at must be < date_to+1 day AND ends_at must be > date_from
        # (overlap condition: booking overlaps [date_from, date_to])
        from_dt = datetime(date_from.year, date_from.month, date_from.day, 0, 0, tzinfo=TZ)
        clauses.append(Booking.ends_at > from_dt)
    if date_to is not None:
        to_dt = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=TZ)
        clauses.append(Booking.starts_at <= to_dt)
    if booking_status is not None:
        clauses.append(Booking.status == booking_status)
    if organizer:
        clauses.append(User.full_name.ilike(f"%{organizer}%"))
    return clauses


@router.get("", response_model=AdminBookingListOut)
async def admin_list_bookings(
    db: SessionDep,
    current_user: AdminUser,
    room_id: uuid.UUID | None = Query(default=None),
    organizer_id: uuid.UUID | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    booking_status: str | None = Query(default=None, alias="status"),
    organizer: str | None = Query(default=None, description="organizer name contains (case-insensitive)"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    sort: str | None = Query(default=None),
    dir: Literal["asc", "desc"] = Query(default="asc"),
) -> AdminBookingListOut:
    """List all bookings with admin visibility.

    Returns {items: [BookingAdminOut], total: int}.
    Resolves organizer_name via a single JOIN (no N+1).
    """
    check_sort(sort, BOOKING_SORTABLE)
    clauses = _build_filter_clauses(room_id, organizer_id, date_from, date_to, booking_status,
                                    organizer)

    # Count query — same outer join as the data query (users.id is unique, so
    # the count is unchanged) so the organizer-name filter applies to it too.
    count_stmt = (
        select(func.count())
        .select_from(Booking)
        .join(User, Booking.organizer_id == User.id, isouter=True)
    )
    if clauses:
        count_stmt = count_stmt.where(and_(*clauses))
    total_result = await db.execute(count_stmt)
    total: int = total_result.scalar_one()

    # Data query: JOIN room + user (no N+1)
    stmt = (
        select(Booking, MeetingRoom, User.full_name.label("organizer_name"))
        .join(MeetingRoom, Booking.room_id == MeetingRoom.id)
        .join(User, Booking.organizer_id == User.id, isouter=True)
    )
    stmt = apply_sort(stmt, sort, dir, BOOKING_SORTABLE, tiebreak=Booking.id,
                      default=(Booking.starts_at.desc(),)).offset(offset).limit(limit)
    if clauses:
        stmt = stmt.where(and_(*clauses))

    result = await db.execute(stmt)
    rows = result.all()

    items: list[BookingAdminOut] = []
    for booking, room, organizer_name in rows:
        items.append(
            BookingAdminOut(
                id=booking.id,
                title=booking.title,
                description=booking.description,
                starts_at=booking.starts_at,
                ends_at=booking.ends_at,
                organizer_name=organizer_name or "Unknown",
                room_id=booking.room_id,
                room_name=room.name,
                room_code=room.code,
                status=booking.status,
                attendee_ids=[uuid.UUID(str(aid)) for aid in (booking.attendee_ids or [])],
                series_id=booking.series_id,
                rrule=booking.rrule,
                sync_status=booking.sync_status,
                ical_sequence=booking.ical_sequence,
            )
        )

    return AdminBookingListOut(items=items, total=total)


@router.get("/export")
async def admin_export_bookings(
    db: SessionDep,
    current_user: AdminUser,
    room_id: uuid.UUID | None = Query(default=None),
    organizer_id: uuid.UUID | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    booking_status: str | None = Query(default=None, alias="status"),
    organizer: str | None = Query(default=None),
) -> StreamingResponse:
    """Export all matching bookings as CSV (no pagination cap).

    Columns: id,room_code,room_name,title,organizer,starts_at,ends_at,
             status,attendees_count,sync_status,created_at
    """
    clauses = _build_filter_clauses(room_id, organizer_id, date_from, date_to, booking_status,
                                    organizer)

    stmt = (
        select(Booking, MeetingRoom, User.full_name.label("organizer_name"))
        .join(MeetingRoom, Booking.room_id == MeetingRoom.id)
        .join(User, Booking.organizer_id == User.id, isouter=True)
        .order_by(Booking.starts_at.desc())
    )
    if clauses:
        stmt = stmt.where(and_(*clauses))

    result = await db.execute(stmt)
    rows = result.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "room_code", "room_name", "title", "organizer",
        "starts_at", "ends_at", "status", "attendees_count",
        "sync_status", "created_at",
    ])
    for booking, room, organizer_name in rows:
        # Render datetimes in DISPLAY_TIMEZONE (config contract: "exports render in
        # this zone") so exported times match what users see in the UI, not UTC.
        writer.writerow([
            str(booking.id),
            room.code,
            room.name,
            booking.title,
            organizer_name or "Unknown",
            booking.starts_at.astimezone(TZ).isoformat(),
            booking.ends_at.astimezone(TZ).isoformat(),
            booking.status,
            len(booking.attendee_ids or []),
            booking.sync_status,
            booking.created_at.astimezone(TZ).isoformat(),
        ])

    csv_content = output.getvalue()
    output.close()

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bookings_export.csv"},
    )
