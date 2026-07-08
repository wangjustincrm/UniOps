"""TDD tests for GET /bookings/day (day-summary endpoint).

Covers:
  - rooms[]: non-disabled rooms sorted floor/name; disabled excluded; maintenance included
  - bookings[]: confirmed bookings overlapping local day window only
  - cancelled bookings excluded
  - next-day booking (local time) excluded
  - local-day boundary: booking at 23:00 local time on target day -> included
  - UTC-midnight cross: booking starts before UTC midnight but is on the next local day -> excluded
  - response shape: date, open_start, open_end, rooms, bookings
  - bad date format -> 422
  - unauthenticated -> 403
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import authed_client, make_token, make_user

TZ = ZoneInfo("America/Toronto")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Return a tz-aware datetime in America/Toronto."""
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


async def _create_room(admin_client, *, status: str = "available", floor: str = "1", name_prefix: str = "Room") -> dict:
    payload = {
        "name": f"{name_prefix}-{uuid.uuid4().hex[:6]}",
        "code": f"DS-{uuid.uuid4().hex[:6]}",
        "campus": "Main",
        "building": "HQ",
        "floor": floor,
        "area": "West",
        "capacity": 8,
        "equipment": [],
        "room_type": "standard",
        "open_time_start": "08:00:00",
        "open_time_end": "20:00:00",
    }
    r = await admin_client.post("/api/v1/admin/rooms", json=payload)
    assert r.status_code == 201, r.text
    room = r.json()
    if status != "available":
        sr = await admin_client.post(
            f"/api/v1/admin/rooms/{room['id']}/status",
            json={"status": status, "notes": "test"},
        )
        assert sr.status_code == 200, sr.text
    return room


async def _insert_booking(db_session, room_id: str, starts_at: datetime, ends_at: datetime,
                          status: str = "confirmed", title: str = "Test Meeting") -> None:
    from app.models.booking import Booking as BookingModel
    b = BookingModel(
        room_id=uuid.UUID(room_id),
        title=title,
        organizer_id=uuid.uuid4(),
        attendee_ids=[],
        starts_at=starts_at.astimezone(timezone.utc),
        ends_at=ends_at.astimezone(timezone.utc),
        status=status,
        calendar_uid=f"ds-{uuid.uuid4()}@test",
    )
    db_session.add(b)
    await db_session.flush()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestDaySummaryRooms:
    """GET /bookings/day — rooms[] field."""

    async def test_disabled_room_excluded(self, admin, requester):
        _, adm = admin
        user, req = requester
        # Create one available room and one disabled room
        r_avail = await _create_room(adm, status="available", name_prefix="Avail")
        r_disabled = await _create_room(adm, status="disabled", name_prefix="Disabled")

        resp = await req.get("/api/v1/bookings/day", params={"date": "2026-08-01"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        room_ids = {r["id"] for r in data["rooms"]}
        assert r_avail["id"] in room_ids
        assert r_disabled["id"] not in room_ids

    async def test_maintenance_room_included(self, admin, requester):
        _, adm = admin
        user, req = requester
        r_maint = await _create_room(adm, status="maintenance", name_prefix="Maint")

        resp = await req.get("/api/v1/bookings/day", params={"date": "2026-08-01"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        room_ids = {r["id"] for r in data["rooms"]}
        assert r_maint["id"] in room_ids
        matching = next(r for r in data["rooms"] if r["id"] == r_maint["id"])
        assert matching["status"] == "maintenance"

    async def test_rooms_sorted_by_floor_then_name(self, admin, requester):
        _, adm = admin
        _, req = requester
        # Create rooms on different floors with known names
        r_f2_z = await _create_room(adm, floor="2", name_prefix="ZZZ")
        r_f1_a = await _create_room(adm, floor="1", name_prefix="AAA")
        r_f1_b = await _create_room(adm, floor="1", name_prefix="BBB")

        resp = await req.get("/api/v1/bookings/day", params={"date": "2026-08-02"})
        assert resp.status_code == 200, resp.text
        rooms = resp.json()["rooms"]
        ids = [r["id"] for r in rooms]
        # Floor 1 rooms must appear before floor 2
        assert ids.index(r_f1_a["id"]) < ids.index(r_f2_z["id"])
        assert ids.index(r_f1_b["id"]) < ids.index(r_f2_z["id"])
        # Within floor 1: AAA before BBB
        assert ids.index(r_f1_a["id"]) < ids.index(r_f1_b["id"])


class TestDaySummaryBookings:
    """GET /bookings/day — bookings[] field."""

    async def test_confirmed_booking_on_target_day_included(self, admin, requester, db_session):
        _, adm = admin
        _, req = requester
        room = await _create_room(adm)
        target = "2026-09-15"
        # 10:00-11:00 local on target day
        starts = _local(2026, 9, 15, 10, 0)
        ends   = _local(2026, 9, 15, 11, 0)
        await _insert_booking(db_session, room["id"], starts, ends)

        resp = await req.get("/api/v1/bookings/day", params={"date": target})
        assert resp.status_code == 200, resp.text
        bookings = resp.json()["bookings"]
        matching = [b for b in bookings if b["room_id"] == room["id"]]
        assert len(matching) == 1

    async def test_cancelled_booking_excluded(self, admin, requester, db_session):
        _, adm = admin
        _, req = requester
        room = await _create_room(adm)
        target = "2026-09-16"
        starts = _local(2026, 9, 16, 14, 0)
        ends   = _local(2026, 9, 16, 15, 0)
        await _insert_booking(db_session, room["id"], starts, ends, status="cancelled")

        resp = await req.get("/api/v1/bookings/day", params={"date": target})
        assert resp.status_code == 200
        bookings = resp.json()["bookings"]
        matching = [b for b in bookings if b["room_id"] == room["id"]]
        assert len(matching) == 0

    async def test_next_day_booking_excluded(self, admin, requester, db_session):
        _, adm = admin
        _, req = requester
        room = await _create_room(adm)
        target = "2026-09-17"
        # 09:00-10:00 local on the NEXT day
        starts = _local(2026, 9, 18, 9, 0)
        ends   = _local(2026, 9, 18, 10, 0)
        await _insert_booking(db_session, room["id"], starts, ends)

        resp = await req.get("/api/v1/bookings/day", params={"date": target})
        assert resp.status_code == 200
        bookings = resp.json()["bookings"]
        matching = [b for b in bookings if b["room_id"] == room["id"]]
        assert len(matching) == 0

    async def test_two_rooms_both_appear(self, admin, requester, db_session):
        _, adm = admin
        _, req = requester
        room_a = await _create_room(adm)
        room_b = await _create_room(adm)
        target = "2026-09-20"
        await _insert_booking(db_session, room_a["id"], _local(2026, 9, 20, 9, 0),  _local(2026, 9, 20, 10, 0))
        await _insert_booking(db_session, room_b["id"], _local(2026, 9, 20, 13, 0), _local(2026, 9, 20, 14, 0))

        resp = await req.get("/api/v1/bookings/day", params={"date": target})
        assert resp.status_code == 200
        room_ids_with_bookings = {b["room_id"] for b in resp.json()["bookings"]}
        assert room_a["id"] in room_ids_with_bookings
        assert room_b["id"] in room_ids_with_bookings

    async def test_local_day_boundary_late_booking_included(self, admin, requester, db_session):
        """A booking at 23:00 local time on the target day must be included.

        America/Toronto is UTC-4 in summer (EDT). 2026-10-05 23:00 local =
        2026-10-06 03:00 UTC — crossing the UTC date line but still within
        the LOCAL calendar day window [Oct 5 00:00-Oct 6 00:00 local).
        """
        _, adm = admin
        _, req = requester
        room = await _create_room(adm)
        target = "2026-10-05"
        # 23:00-23:45 local -> well inside the local day but in the next UTC day
        starts = _local(2026, 10, 5, 23, 0)
        ends   = _local(2026, 10, 5, 23, 45)
        await _insert_booking(db_session, room["id"], starts, ends)

        resp = await req.get("/api/v1/bookings/day", params={"date": target})
        assert resp.status_code == 200
        bookings = resp.json()["bookings"]
        matching = [b for b in bookings if b["room_id"] == room["id"]]
        assert len(matching) == 1, "Late-night local booking must be included in the correct day"

    async def test_utc_midnight_cross_not_on_local_day_excluded(self, admin, requester, db_session):
        """A booking on the NEXT local day is excluded.

        2026-10-06 01:00 local (EDT, UTC-4) = 2026-10-06 05:00 UTC.
        Querying for 2026-10-05 must NOT return this booking.
        """
        _, adm = admin
        _, req = requester
        room = await _create_room(adm)
        target = "2026-10-05"
        # 01:00 local on Oct 6 - next local day
        starts = _local(2026, 10, 6, 1, 0)
        ends   = _local(2026, 10, 6, 2, 0)
        await _insert_booking(db_session, room["id"], starts, ends)

        resp = await req.get("/api/v1/bookings/day", params={"date": target})
        assert resp.status_code == 200
        bookings = resp.json()["bookings"]
        matching = [b for b in bookings if b["room_id"] == room["id"]]
        assert len(matching) == 0, "Oct 6 local booking must not appear on Oct 5 query"


class TestDaySummaryShape:
    """GET /bookings/day — response shape validation."""

    async def test_response_shape(self, requester):
        _, req = requester
        resp = await req.get("/api/v1/bookings/day", params={"date": "2026-11-01"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == "2026-11-01"
        assert "open_start" in data   # e.g. "08:00"
        assert "open_end" in data     # e.g. "20:00"
        assert isinstance(data["rooms"], list)
        assert isinstance(data["bookings"], list)

    async def test_bad_date_format_returns_422(self, requester):
        _, req = requester
        resp = await req.get("/api/v1/bookings/day", params={"date": "not-a-date"})
        assert resp.status_code == 422

    async def test_unauthenticated_returns_403(self, client):
        resp = await client.get("/api/v1/bookings/day", params={"date": "2026-11-01"})
        assert resp.status_code == 403

    async def test_booking_slim_fields_present(self, admin, requester, db_session):
        _, adm = admin
        _, req = requester
        room = await _create_room(adm)
        await _insert_booking(
            db_session, room["id"],
            _local(2026, 11, 10, 9, 0),
            _local(2026, 11, 10, 10, 0),
            title="Shape Test Meeting",
        )
        resp = await req.get("/api/v1/bookings/day", params={"date": "2026-11-10"})
        assert resp.status_code == 200
        bookings = resp.json()["bookings"]
        matching = [b for b in bookings if b["room_id"] == room["id"]]
        assert len(matching) == 1
        b = matching[0]
        assert "id" in b
        assert "title" in b
        assert "starts_at" in b
        assert "ends_at" in b
        assert "organizer_name" in b
        assert "room_id" in b
        assert b["title"] == "Shape Test Meeting"
