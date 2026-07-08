import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.booking import Booking
from app.models.room import MeetingRoom


def _room(**kw):
    return MeetingRoom(name="R1", code=kw.pop("code", "R1"), capacity=8, **kw)


def _booking(room_id, start, end, **kw):
    return Booking(
        room_id=room_id, title="t", organizer_id=uuid.uuid4(),
        starts_at=start, ends_at=end, calendar_uid=str(uuid.uuid4()), **kw,
    )


@pytest.mark.asyncio
async def test_overlapping_confirmed_bookings_rejected(db_session):
    room = _room()
    db_session.add(room)
    await db_session.flush()
    t0 = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    db_session.add(_booking(room.id, t0, t0 + timedelta(hours=1)))
    await db_session.flush()
    db_session.add(_booking(room.id, t0 + timedelta(minutes=30), t0 + timedelta(hours=2)))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_cancelled_booking_frees_slot(db_session):
    room = _room(code="R2")
    db_session.add(room)
    await db_session.flush()
    t0 = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    db_session.add(_booking(room.id, t0, t0 + timedelta(hours=1), status="cancelled"))
    await db_session.flush()
    db_session.add(_booking(room.id, t0, t0 + timedelta(hours=1)))
    await db_session.flush()  # no error


@pytest.mark.asyncio
async def test_back_to_back_ok(db_session):
    room = _room(code="R3")
    db_session.add(room)
    await db_session.flush()
    t0 = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    db_session.add(_booking(room.id, t0, t0 + timedelta(hours=1)))
    db_session.add(_booking(room.id, t0 + timedelta(hours=1), t0 + timedelta(hours=2)))
    await db_session.flush()  # [14:00,15:00) and [15:00,16:00) do not overlap
