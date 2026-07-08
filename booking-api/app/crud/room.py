"""CRUD operations for MeetingRoom."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.room import MeetingRoom
from app.models.booking import Booking
from app.schemas.room import RoomCreate, RoomUpdate


async def create_room(db: AsyncSession, data: RoomCreate) -> MeetingRoom:
    """Create a new room. Raises IntegrityError on duplicate code."""
    room = MeetingRoom(**data.model_dump())
    db.add(room)
    await db.flush()  # raises IntegrityError on unique violation before commit
    await db.refresh(room)
    return room


async def get_room(db: AsyncSession, room_id: uuid.UUID) -> MeetingRoom | None:
    result = await db.execute(select(MeetingRoom).where(MeetingRoom.id == room_id))
    return result.scalar_one_or_none()


async def list_rooms(
    db: AsyncSession,
    *,
    floor: str | None = None,
    area: str | None = None,
    status: str | None = None,
) -> list[MeetingRoom]:
    q = select(MeetingRoom)
    if floor is not None:
        q = q.where(MeetingRoom.floor == floor)
    if area is not None:
        q = q.where(MeetingRoom.area == area)
    if status is not None:
        q = q.where(MeetingRoom.status == status)
    result = await db.execute(q.order_by(MeetingRoom.name))
    return list(result.scalars().all())


async def update_room(
    db: AsyncSession, room: MeetingRoom, data: RoomUpdate
) -> MeetingRoom:
    """Apply only the provided fields (excludes unset)."""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(room, field, value)
    await db.flush()
    await db.refresh(room)
    return room


async def change_room_status(
    db: AsyncSession, room: MeetingRoom, status: str, notes: str | None
) -> tuple[MeetingRoom, int]:
    """Update room status and count affected future confirmed bookings."""
    room.status = status
    if notes is not None:
        room.notes = notes
    await db.flush()

    now = datetime.now(timezone.utc)
    count_q = (
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.room_id == room.id,
            Booking.status == "confirmed",
            Booking.starts_at > now,
        )
    )
    affected = (await db.execute(count_q)).scalar_one()
    await db.refresh(room)
    return room, affected
