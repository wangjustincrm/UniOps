"""Employee-facing room listing, detail and availability endpoints.

Route ordering is critical — /rooms/availability MUST be declared BEFORE /rooms/{id}
so FastAPI does not match the literal string "availability" as a UUID path param.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import SessionDep
from app.core.permissions import CurrentUser
from app.crud.config import get_or_create_config
from app.models.booking import Booking
from app.models.room import MeetingRoom
from app.models.user_mirror import User
from app.schemas.booking import BookingSlimOut
from app.schemas.room import RoomOut
from app.services.availability import compute_room_status

router = APIRouter()


# ── Output schemas ─────────────────────────────────────────────────────────────

class RoomWithStatusOut(RoomOut):
    status_now: str
    next_meeting_at: datetime | None = None


class RoomDetailOut(RoomWithStatusOut):
    today_bookings: list[BookingSlimOut]
    week_bookings: list[BookingSlimOut]


# ── Shared filter dependency ───────────────────────────────────────────────────

def _apply_room_filters(
    stmt,
    campus: str | None,
    building: str | None,
    floor: str | None,
    area: str | None,
    min_capacity: int | None,
    equipment: list[str] | None,
    room_type: str | None,
):
    """Apply common room filter clauses to a SELECT statement."""
    if campus is not None:
        stmt = stmt.where(MeetingRoom.campus == campus)
    if building is not None:
        stmt = stmt.where(MeetingRoom.building == building)
    if floor is not None:
        stmt = stmt.where(MeetingRoom.floor == floor)
    if area is not None:
        stmt = stmt.where(MeetingRoom.area == area)
    if min_capacity is not None:
        stmt = stmt.where(MeetingRoom.capacity >= min_capacity)
    if equipment:
        # JSONB containment: room.equipment must contain ALL requested items
        for item in equipment:
            stmt = stmt.where(MeetingRoom.equipment.contains([item]))
    if room_type is not None:
        stmt = stmt.where(MeetingRoom.room_type == room_type)
    return stmt


async def _load_today_bookings_by_rooms(
    db: AsyncSession,
    room_ids: list[uuid.UUID],
    today_start: datetime,
    today_end: datetime,
) -> dict[uuid.UUID, list[Booking]]:
    """One query: all confirmed/cancelled bookings for given rooms today."""
    if not room_ids:
        return {}
    result = await db.execute(
        select(Booking)
        .where(
            and_(
                Booking.room_id.in_(room_ids),
                Booking.starts_at < today_end,
                Booking.ends_at > today_start,
            )
        )
        .order_by(Booking.starts_at)
    )
    bookings = result.scalars().all()
    grouped: dict[uuid.UUID, list[Booking]] = {rid: [] for rid in room_ids}
    for b in bookings:
        if b.room_id in grouped:
            grouped[b.room_id].append(b)
    return grouped


async def _load_week_bookings_by_rooms(
    db: AsyncSession,
    room_ids: list[uuid.UUID],
    today_start: datetime,
    week_end: datetime,
) -> dict[uuid.UUID, list[Booking]]:
    """One query: all confirmed bookings for the next 7 days (today inclusive)."""
    if not room_ids:
        return {}
    result = await db.execute(
        select(Booking)
        .where(
            and_(
                Booking.room_id.in_(room_ids),
                Booking.status == "confirmed",
                Booking.starts_at < week_end,
                Booking.ends_at > today_start,
            )
        )
        .order_by(Booking.starts_at)
    )
    bookings = result.scalars().all()
    grouped: dict[uuid.UUID, list[Booking]] = {rid: [] for rid in room_ids}
    for b in bookings:
        if b.room_id in grouped:
            grouped[b.room_id].append(b)
    return grouped


async def _resolve_organizer_names(
    db: AsyncSession,
    bookings: list[Booking],
) -> dict[uuid.UUID, str]:
    """Return {organizer_id: full_name} for all bookings (one query)."""
    organizer_ids = list({b.organizer_id for b in bookings})
    if not organizer_ids:
        return {}
    result = await db.execute(
        select(User.id, User.full_name).where(User.id.in_(organizer_ids))
    )
    return {row.id: row.full_name for row in result}


def _booking_to_slim(b: Booking, organizer_names: dict[uuid.UUID, str]) -> BookingSlimOut:
    return BookingSlimOut(
        id=b.id,
        title=b.title,
        starts_at=b.starts_at,
        ends_at=b.ends_at,
        organizer_name=organizer_names.get(b.organizer_id, "Unknown"),
    )


def _next_meeting(bookings_today: list[Booking], now: datetime) -> datetime | None:
    """Return starts_at of next confirmed booking after now, or None."""
    future_confirmed = [
        b for b in bookings_today
        if b.status == "confirmed" and b.starts_at > now
    ]
    if not future_confirmed:
        return None
    return min(b.starts_at for b in future_confirmed)


# ─────────────────────────────────────────────────────────────────────────────
# IMPORTANT: /rooms/availability MUST be declared before /rooms/{id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/availability", response_model=list[RoomWithStatusOut])
async def get_available_rooms(
    db: SessionDep,
    _user: CurrentUser,
    starts_at: datetime = Query(...),
    ends_at: datetime = Query(...),
    campus: str | None = None,
    building: str | None = None,
    floor: str | None = None,
    area: str | None = None,
    min_capacity: int | None = None,
    equipment: list[str] = Query(default=[]),
    room_type: str | None = None,
) -> Any:
    """Return rooms with no confirmed booking overlapping [starts_at, ends_at).

    Overlap predicate: booking.starts_at < ends_at AND booking.ends_at > starts_at
    (touching edges are NOT a conflict).
    Cancelled bookings are ignored.
    """
    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)
    now = datetime.now(tz)

    # Query rooms matching filters (only available-status rooms)
    stmt = select(MeetingRoom).where(MeetingRoom.status == "available")
    stmt = _apply_room_filters(stmt, campus, building, floor, area, min_capacity, equipment, room_type)
    result = await db.execute(stmt)
    rooms = result.scalars().all()

    if not rooms:
        return []

    room_ids = [r.id for r in rooms]

    # Find rooms that have a confirmed booking overlapping the requested window
    conflicts_result = await db.execute(
        select(Booking.room_id)
        .where(
            and_(
                Booking.room_id.in_(room_ids),
                Booking.status == "confirmed",
                Booking.starts_at < ends_at,
                Booking.ends_at > starts_at,
            )
        )
    )
    conflicting_room_ids = {row.room_id for row in conflicts_result}

    # Filter to non-conflicting rooms
    available_rooms = [r for r in rooms if r.id not in conflicting_room_ids]

    if not available_rooms:
        return []

    # Compute today's status for available rooms
    avail_ids = [r.id for r in available_rooms]
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    today_bookings_map = await _load_today_bookings_by_rooms(db, avail_ids, today_start, today_end)

    output = []
    for room in available_rooms:
        bookings_today = today_bookings_map.get(room.id, [])
        status_now = compute_room_status(room, bookings_today, now)
        next_at = _next_meeting(bookings_today, now)
        out = RoomWithStatusOut(
            **RoomOut.model_validate(room).model_dump(),
            status_now=status_now,
            next_meeting_at=next_at,
        )
        output.append(out)

    return output


@router.get("", response_model=list[RoomWithStatusOut])
async def list_rooms_employee(
    db: SessionDep,
    _user: CurrentUser,
    campus: str | None = None,
    building: str | None = None,
    floor: str | None = None,
    area: str | None = None,
    min_capacity: int | None = None,
    equipment: list[str] = Query(default=[]),
    room_type: str | None = None,
) -> Any:
    """List rooms with real-time status. All authenticated users have access."""
    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)
    now = datetime.now(tz)

    # Fetch rooms matching filters
    stmt = select(MeetingRoom)
    stmt = _apply_room_filters(stmt, campus, building, floor, area, min_capacity, equipment, room_type)
    result = await db.execute(stmt)
    rooms = result.scalars().all()

    if not rooms:
        return []

    room_ids = [r.id for r in rooms]

    # Batch-load today's bookings to avoid N+1
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    today_bookings_map = await _load_today_bookings_by_rooms(db, room_ids, today_start, today_end)

    output = []
    for room in rooms:
        bookings_today = today_bookings_map.get(room.id, [])
        status_now = compute_room_status(room, bookings_today, now)
        next_at = _next_meeting(bookings_today, now)
        out = RoomWithStatusOut(
            **RoomOut.model_validate(room).model_dump(),
            status_now=status_now,
            next_meeting_at=next_at,
        )
        output.append(out)

    return output


@router.get("/{room_id}", response_model=RoomDetailOut)
async def get_room_detail(
    room_id: uuid.UUID,
    db: SessionDep,
    _user: CurrentUser,
) -> Any:
    """Return room detail with real-time status plus today's and next-7-day bookings."""
    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)
    now = datetime.now(tz)

    # Fetch the room
    result = await db.execute(select(MeetingRoom).where(MeetingRoom.id == room_id))
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    # Date boundaries
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    week_end = today_start + timedelta(days=7)

    # Batch: today's all-status bookings (for status computation)
    today_result = await db.execute(
        select(Booking)
        .where(
            and_(
                Booking.room_id == room_id,
                Booking.starts_at < today_end,
                Booking.ends_at > today_start,
            )
        )
        .order_by(Booking.starts_at)
    )
    bookings_today_all = today_result.scalars().all()

    # Week bookings (confirmed only, today through +7 days)
    week_result = await db.execute(
        select(Booking)
        .where(
            and_(
                Booking.room_id == room_id,
                Booking.status == "confirmed",
                Booking.starts_at < week_end,
                Booking.ends_at > today_start,
            )
        )
        .order_by(Booking.starts_at)
    )
    bookings_week = week_result.scalars().all()

    # Resolve organizer names in one query
    all_bookings = list(bookings_today_all) + [b for b in bookings_week if b not in bookings_today_all]
    organizer_names = await _resolve_organizer_names(db, all_bookings)

    # Today bookings for display (confirmed only)
    today_confirmed = [b for b in bookings_today_all if b.status == "confirmed"]
    today_slim = [_booking_to_slim(b, organizer_names) for b in today_confirmed]
    week_slim = [_booking_to_slim(b, organizer_names) for b in bookings_week]

    # Compute status
    status_now = compute_room_status(room, bookings_today_all, now)
    next_at = _next_meeting(bookings_today_all, now)

    return RoomDetailOut(
        **RoomOut.model_validate(room).model_dump(),
        status_now=status_now,
        next_meeting_at=next_at,
        today_bookings=today_slim,
        week_bookings=week_slim,
    )
