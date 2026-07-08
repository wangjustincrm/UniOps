"""TDD tests for availability service (pure functions) and employee room endpoints.

Step 1 (RED): Write all tests FIRST — they must fail before implementation exists.
Step 2 (GREEN): Implement app/services/availability.py and app/api/v1/rooms.py.
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

# ── Pure-function imports ─────────────────────────────────────────────────────
# These will fail with ImportError until availability.py is created.
from app.services.availability import compute_room_status, free_slots

# ── Helpers to build in-memory MeetingRoom / Booking objects ─────────────────
# compute_room_status / free_slots are pure functions; they only read attributes.
# SQLAlchemy ORM models need a full session to be properly instantiated via
# __new__.  Use types.SimpleNamespace so we can build attribute-duck-typed
# stand-ins without touching the DB.

import types

TZ = ZoneInfo("America/Toronto")


def _room(**kwargs) -> types.SimpleNamespace:
    """Build an in-memory duck-typed room (no DB, no ORM session needed)."""
    defaults = dict(
        id=uuid.uuid4(),
        name="Test Room",
        code=f"TR-{uuid.uuid4().hex[:4]}",
        capacity=10,
        equipment=[],
        room_type="standard",
        status="available",
        image_file_ids=[],
        open_time_start=None,
        open_time_end=None,
        advance_booking_days=None,
        campus=None,
        building=None,
        floor=None,
        area=None,
        owner_department=None,
        notes=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


def _booking(
    room_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    status: str = "confirmed",
) -> types.SimpleNamespace:
    """Build an in-memory duck-typed booking (no DB, no ORM session needed)."""
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        room_id=room_id,
        title="Test Meeting",
        description=None,
        organizer_id=uuid.uuid4(),
        attendee_ids=[],
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
        series_id=None,
        rrule=None,
        calendar_uid=f"test-{uuid.uuid4()}@test",
        ical_sequence=0,
        sync_status="pending",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _dt(h: int, m: int = 0, day_offset: int = 0) -> datetime:
    """Return a tz-aware datetime in America/Toronto for today + day_offset."""
    today = datetime.now(TZ).date() + timedelta(days=day_offset)
    return datetime(today.year, today.month, today.day, h, m, tzinfo=TZ)


# ═══════════════════════════════════════════════════════════════════════════════
# Section 1: compute_room_status — pure function
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeRoomStatusDisabled:
    """room.status='disabled' → always 'disabled', regardless of bookings."""

    def test_disabled_room_no_bookings(self):
        room = _room(status="disabled")
        now = _dt(10, 0)
        assert compute_room_status(room, [], now) == "disabled"

    def test_disabled_room_with_active_booking(self):
        room = _room(status="disabled")
        now = _dt(10, 0)
        b = _booking(room.id, _dt(9, 0), _dt(11, 0))  # currently in_use window
        assert compute_room_status(room, [b], now) == "disabled"

    def test_disabled_takes_precedence_over_maintenance_check(self):
        """disabled room with status 'disabled' must return 'disabled'."""
        room = _room(status="disabled")
        now = _dt(14, 30)
        b = _booking(room.id, _dt(14, 45), _dt(15, 30))  # starting_soon window
        assert compute_room_status(room, [b], now) == "disabled"


class TestComputeRoomStatusMaintenance:
    """room.status='maintenance' → always 'maintenance'."""

    def test_maintenance_room_no_bookings(self):
        room = _room(status="maintenance")
        now = _dt(9, 0)
        assert compute_room_status(room, [], now) == "maintenance"

    def test_maintenance_with_booking(self):
        room = _room(status="maintenance")
        now = _dt(10, 0)
        b = _booking(room.id, _dt(10, 0), _dt(11, 0))
        assert compute_room_status(room, [b], now) == "maintenance"

    def test_maintenance_takes_precedence_over_in_use(self):
        room = _room(status="maintenance")
        now = _dt(10, 30)
        b = _booking(room.id, _dt(10, 0), _dt(11, 0))  # now inside window
        assert compute_room_status(room, [b], now) == "maintenance"


class TestComputeRoomStatusInUse:
    """now is inside a confirmed booking window → 'in_use'."""

    def test_in_use_at_start_boundary(self):
        room = _room()
        now = _dt(10, 0)
        b = _booking(room.id, _dt(10, 0), _dt(11, 0))
        assert compute_room_status(room, [b], now) == "in_use"

    def test_in_use_in_middle_of_booking(self):
        room = _room()
        now = _dt(10, 30)
        b = _booking(room.id, _dt(10, 0), _dt(11, 0))
        assert compute_room_status(room, [b], now) == "in_use"

    def test_not_in_use_at_end_boundary(self):
        """ends_at is exclusive: now == ends_at → NOT in_use."""
        room = _room()
        now = _dt(11, 0)
        b = _booking(room.id, _dt(10, 0), _dt(11, 0))
        # should NOT be in_use — booking ended
        status = compute_room_status(room, [b], now)
        assert status != "in_use"

    def test_in_use_ignores_cancelled_booking(self):
        room = _room()
        now = _dt(10, 30)
        b = _booking(room.id, _dt(10, 0), _dt(11, 0), status="cancelled")
        # cancelled booking does not make room in_use
        assert compute_room_status(room, [b], now) != "in_use"

    def test_in_use_takes_precedence_over_starting_soon(self):
        """If now is inside a booking AND there's a next booking ≤15 min away, in_use wins."""
        room = _room()
        now = _dt(10, 50)
        current = _booking(room.id, _dt(10, 0), _dt(11, 0))
        next_b = _booking(room.id, _dt(11, 0), _dt(12, 0))
        assert compute_room_status(room, [current, next_b], now) == "in_use"


class TestComputeRoomStatusStartingSoon:
    """Next confirmed booking starts within ≤ 15 minutes → 'starting_soon'."""

    def test_starting_soon_exactly_15_min(self):
        room = _room()
        now = _dt(10, 0)
        b = _booking(room.id, _dt(10, 15), _dt(11, 0))
        assert compute_room_status(room, [b], now) == "starting_soon"

    def test_starting_soon_1_min(self):
        room = _room()
        now = _dt(10, 14)
        b = _booking(room.id, _dt(10, 15), _dt(11, 0))
        assert compute_room_status(room, [b], now) == "starting_soon"

    def test_not_starting_soon_at_16_min(self):
        """16 minutes away → NOT starting_soon (should be booked or free)."""
        room = _room()
        now = _dt(9, 59)
        b = _booking(room.id, _dt(10, 15), _dt(11, 0))
        status = compute_room_status(room, [b], now)
        assert status != "starting_soon"

    def test_starting_soon_ignores_cancelled(self):
        room = _room()
        now = _dt(10, 5)
        b = _booking(room.id, _dt(10, 15), _dt(11, 0), status="cancelled")
        status = compute_room_status(room, [b], now)
        assert status != "starting_soon"


class TestComputeRoomStatusBooked:
    """Any future confirmed booking today (but > 15 min away) → 'booked'."""

    def test_booked_future_meeting_today(self):
        room = _room()
        now = _dt(9, 0)
        b = _booking(room.id, _dt(14, 0), _dt(15, 0))  # 5 hours away → booked
        assert compute_room_status(room, [b], now) == "booked"

    def test_booked_exactly_16_minutes_away(self):
        room = _room()
        now = _dt(9, 44)
        b = _booking(room.id, _dt(10, 0), _dt(11, 0))  # 16 min away
        assert compute_room_status(room, [b], now) == "booked"


class TestComputeRoomStatusFree:
    """No confirmed bookings remaining today → 'free'."""

    def test_free_no_bookings(self):
        room = _room()
        now = _dt(10, 0)
        assert compute_room_status(room, [], now) == "free"

    def test_free_only_cancelled_bookings(self):
        room = _room()
        now = _dt(10, 0)
        b = _booking(room.id, _dt(11, 0), _dt(12, 0), status="cancelled")
        assert compute_room_status(room, [b], now) == "free"

    def test_free_after_last_booking_ends(self):
        """Past bookings (ends_at < now) don't block free status."""
        room = _room()
        now = _dt(15, 0)
        b = _booking(room.id, _dt(9, 0), _dt(10, 0))  # ended 5 hours ago
        assert compute_room_status(room, [b], now) == "free"


# ═══════════════════════════════════════════════════════════════════════════════
# Section 2: free_slots — pure function
# ═══════════════════════════════════════════════════════════════════════════════


class TestFreeSlots:
    """free_slots(room_open, busy, day, slot_minutes, tz) → list of (start, end) tuples."""

    def _day(self) -> date:
        return datetime.now(TZ).date()

    def _open(self) -> tuple[time, time]:
        return (time(8, 0), time(20, 0))

    def test_no_busy_returns_full_open_hours(self):
        """With no busy periods, the whole open window is one big free slot."""
        day = self._day()
        slots = free_slots(self._open(), [], day, 15, TZ)
        # Should have at least one slot covering the full open window
        assert len(slots) >= 1
        # Start of first slot = open start
        open_start = datetime(day.year, day.month, day.day, 8, 0, tzinfo=TZ)
        open_end = datetime(day.year, day.month, day.day, 20, 0, tzinfo=TZ)
        assert slots[0][0] == open_start
        assert slots[-1][1] == open_end

    def test_one_busy_period_splits_into_two_gaps(self):
        """08:00–20:00 with busy 14:00–15:00 → gaps [08:00–14:00] and [15:00–20:00]."""
        day = self._day()
        busy_start = datetime(day.year, day.month, day.day, 14, 0, tzinfo=TZ)
        busy_end = datetime(day.year, day.month, day.day, 15, 0, tzinfo=TZ)
        slots = free_slots(self._open(), [(busy_start, busy_end)], day, 15, TZ)
        # The two gaps must be present
        open_start = datetime(day.year, day.month, day.day, 8, 0, tzinfo=TZ)
        open_end = datetime(day.year, day.month, day.day, 20, 0, tzinfo=TZ)
        # Find gap before busy: should start at 08:00, end at 14:00
        before = [s for s in slots if s[0] == open_start]
        assert len(before) >= 1
        assert before[0][1] == busy_start
        # Find gap after busy: should start at 15:00, end at 20:00
        after = [s for s in slots if s[1] == open_end]
        assert len(after) >= 1
        assert after[0][0] == busy_end

    def test_busy_before_open_hours_is_ignored(self):
        """Busy 06:00–09:00 → only the portion 08:00–09:00 is blocked."""
        day = self._day()
        busy_start = datetime(day.year, day.month, day.day, 6, 0, tzinfo=TZ)
        busy_end = datetime(day.year, day.month, day.day, 9, 0, tzinfo=TZ)
        slots = free_slots(self._open(), [(busy_start, busy_end)], day, 15, TZ)
        open_end = datetime(day.year, day.month, day.day, 20, 0, tzinfo=TZ)
        # Gap should start at 09:00, not 06:00
        gap_start = datetime(day.year, day.month, day.day, 9, 0, tzinfo=TZ)
        assert all(s[0] >= gap_start for s in slots)
        # Last slot should end at 20:00
        assert slots[-1][1] == open_end

    def test_busy_after_open_hours_is_ignored(self):
        """Busy 19:00–21:00 → only the portion 19:00–20:00 is blocked."""
        day = self._day()
        busy_start = datetime(day.year, day.month, day.day, 19, 0, tzinfo=TZ)
        busy_end = datetime(day.year, day.month, day.day, 21, 0, tzinfo=TZ)
        slots = free_slots(self._open(), [(busy_start, busy_end)], day, 15, TZ)
        open_start = datetime(day.year, day.month, day.day, 8, 0, tzinfo=TZ)
        # First slot should start at 08:00
        assert slots[0][0] == open_start
        # Last slot end ≤ 20:00
        open_end = datetime(day.year, day.month, day.day, 20, 0, tzinfo=TZ)
        assert all(s[1] <= open_end for s in slots)

    def test_slots_are_at_least_slot_minutes_long(self):
        """No returned slot should be shorter than slot_minutes."""
        day = self._day()
        # busy 08:00–19:55 leaves only a 5-min gap at end: too small, excluded
        busy_start = datetime(day.year, day.month, day.day, 8, 0, tzinfo=TZ)
        busy_end = datetime(day.year, day.month, day.day, 19, 55, tzinfo=TZ)
        slots = free_slots(self._open(), [(busy_start, busy_end)], day, 15, TZ)
        for s, e in slots:
            assert (e - s).total_seconds() >= 15 * 60

    def test_fully_booked_day_returns_empty_list(self):
        """If a single booking covers the entire open window, no free slots."""
        day = self._day()
        busy_start = datetime(day.year, day.month, day.day, 8, 0, tzinfo=TZ)
        busy_end = datetime(day.year, day.month, day.day, 20, 0, tzinfo=TZ)
        slots = free_slots(self._open(), [(busy_start, busy_end)], day, 15, TZ)
        assert slots == []

    def test_multiple_busy_periods(self):
        """Two busy periods produce three gaps (or fewer if gaps < slot_minutes)."""
        day = self._day()
        busy1_s = datetime(day.year, day.month, day.day, 9, 0, tzinfo=TZ)
        busy1_e = datetime(day.year, day.month, day.day, 10, 0, tzinfo=TZ)
        busy2_s = datetime(day.year, day.month, day.day, 12, 0, tzinfo=TZ)
        busy2_e = datetime(day.year, day.month, day.day, 13, 0, tzinfo=TZ)
        slots = free_slots(
            self._open(),
            [(busy1_s, busy1_e), (busy2_s, busy2_e)],
            day,
            15,
            TZ,
        )
        open_start = datetime(day.year, day.month, day.day, 8, 0, tzinfo=TZ)
        open_end = datetime(day.year, day.month, day.day, 20, 0, tzinfo=TZ)
        # Gap 1: 08:00–09:00
        gap1 = [s for s in slots if s[0] == open_start]
        assert len(gap1) == 1 and gap1[0][1] == busy1_s
        # Gap 3: 13:00–20:00
        gap3 = [s for s in slots if s[1] == open_end]
        assert len(gap3) == 1 and gap3[0][0] == busy2_e


# ═══════════════════════════════════════════════════════════════════════════════
# Section 3: Employee room endpoints (DB required)
# ═══════════════════════════════════════════════════════════════════════════════

from tests.conftest import make_user, make_token, authed_client


async def _create_room_via_admin(admin_client, **overrides) -> dict:
    """Helper: create a room via admin API and return the JSON."""
    payload = {
        "name": "Test Room",
        "code": f"TR-{uuid.uuid4().hex[:6]}",
        "campus": "Main",
        "building": "HQ",
        "floor": "2",
        "area": "East",
        "capacity": 8,
        "equipment": ["projector"],
        "room_type": "standard",
        "open_time_start": "08:00:00",
        "open_time_end": "20:00:00",
    }
    payload.update(overrides)
    resp = await admin_client.post("/api/v1/admin/rooms", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _insert_booking(test_engine, room_id, organizer_id, starts_at, ends_at, status="confirmed"):
    """Directly insert a booking row via a fresh committed session (visible to HTTP client)."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models.booking import Booking as BookingModel
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    b = BookingModel(
        room_id=uuid.UUID(room_id) if isinstance(room_id, str) else room_id,
        title="Endpoint Test Meeting",
        organizer_id=uuid.UUID(organizer_id) if isinstance(organizer_id, str) else organizer_id,
        attendee_ids=[],
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
        calendar_uid=f"ep-{uuid.uuid4()}@test",
    )
    async with factory() as db:
        db.add(b)
        await db.commit()
        await db.refresh(b)
    return b


class TestGetRoomsEmployee:
    """GET /api/v1/rooms — employee room list."""

    async def test_unauthenticated_returns_401_or_403(self, client):
        resp = await client.get("/api/v1/rooms")
        assert resp.status_code in (401, 403), resp.text

    async def test_requester_can_list_rooms(self, requester, admin, test_engine):
        user, req_client = requester
        adm_user, adm_client = admin
        # create a room
        await _create_room_via_admin(adm_client, code=f"EMP-LIST-{uuid.uuid4().hex[:4]}")
        resp = await req_client.get("/api/v1/rooms")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)

    async def test_room_list_has_status_now_and_next_meeting_at(self, requester, admin, test_engine):
        user, req_client = requester
        adm_user, adm_client = admin
        code = f"STNOW-{uuid.uuid4().hex[:4]}"
        await _create_room_via_admin(adm_client, code=code)
        resp = await req_client.get("/api/v1/rooms")
        assert resp.status_code == 200
        data = resp.json()
        our_room = next((r for r in data if r["code"] == code), None)
        assert our_room is not None
        assert "status_now" in our_room
        assert "next_meeting_at" in our_room
        # free room with no bookings
        assert our_room["status_now"] == "free"
        assert our_room["next_meeting_at"] is None

    async def test_filter_by_campus(self, requester, admin, test_engine):
        user, req_client = requester
        adm_user, adm_client = admin
        code_a = f"CAMP-A-{uuid.uuid4().hex[:4]}"
        code_b = f"CAMP-B-{uuid.uuid4().hex[:4]}"
        await _create_room_via_admin(adm_client, code=code_a, campus="CampusAlpha")
        await _create_room_via_admin(adm_client, code=code_b, campus="CampusBeta")
        resp = await req_client.get("/api/v1/rooms?campus=CampusAlpha")
        assert resp.status_code == 200
        data = resp.json()
        codes = [r["code"] for r in data]
        assert code_a in codes
        assert code_b not in codes

    async def test_filter_by_min_capacity(self, requester, admin, test_engine):
        user, req_client = requester
        adm_user, adm_client = admin
        code_small = f"CAP-S-{uuid.uuid4().hex[:4]}"
        code_large = f"CAP-L-{uuid.uuid4().hex[:4]}"
        await _create_room_via_admin(adm_client, code=code_small, capacity=2)
        await _create_room_via_admin(adm_client, code=code_large, capacity=20)
        resp = await req_client.get("/api/v1/rooms?min_capacity=15")
        assert resp.status_code == 200
        data = resp.json()
        codes = [r["code"] for r in data]
        assert code_small not in codes
        assert code_large in codes

    async def test_filter_by_equipment_containment(self, requester, admin, test_engine):
        """Rooms with equipment superset of requested filter are returned."""
        user, req_client = requester
        adm_user, adm_client = admin
        code_proj = f"EQ-P-{uuid.uuid4().hex[:4]}"
        code_both = f"EQ-PW-{uuid.uuid4().hex[:4]}"
        code_none = f"EQ-N-{uuid.uuid4().hex[:4]}"
        await _create_room_via_admin(adm_client, code=code_proj, equipment=["projector"])
        await _create_room_via_admin(adm_client, code=code_both, equipment=["projector", "whiteboard"])
        await _create_room_via_admin(adm_client, code=code_none, equipment=[])
        # filter: must have projector
        resp = await req_client.get("/api/v1/rooms?equipment=projector")
        assert resp.status_code == 200
        data = resp.json()
        codes = [r["code"] for r in data]
        assert code_proj in codes
        assert code_both in codes
        assert code_none not in codes

    async def test_filter_by_room_type(self, requester, admin, test_engine):
        user, req_client = requester
        adm_user, adm_client = admin
        code_std = f"RT-S-{uuid.uuid4().hex[:4]}"
        code_brd = f"RT-B-{uuid.uuid4().hex[:4]}"
        await _create_room_via_admin(adm_client, code=code_std, room_type="standard")
        await _create_room_via_admin(adm_client, code=code_brd, room_type="boardroom")
        resp = await req_client.get("/api/v1/rooms?room_type=boardroom")
        assert resp.status_code == 200
        data = resp.json()
        codes = [r["code"] for r in data]
        assert code_brd in codes
        assert code_std not in codes


class TestGetRoomDetail:
    """GET /api/v1/rooms/{id} — employee room detail."""

    async def test_room_detail_returns_200(self, requester, admin, test_engine):
        user, req_client = requester
        adm_user, adm_client = admin
        code = f"DET-{uuid.uuid4().hex[:4]}"
        room = await _create_room_via_admin(adm_client, code=code)
        resp = await req_client.get(f"/api/v1/rooms/{room['id']}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["id"] == room["id"]
        assert "status_now" in data
        assert "today_bookings" in data
        assert "week_bookings" in data
        assert isinstance(data["today_bookings"], list)
        assert isinstance(data["week_bookings"], list)

    async def test_room_detail_404_for_unknown_id(self, requester):
        user, req_client = requester
        fake = str(uuid.uuid4())
        resp = await req_client.get(f"/api/v1/rooms/{fake}")
        assert resp.status_code == 404, resp.text

    async def test_room_detail_includes_today_bookings_with_organizer_name(
        self, requester, admin, test_engine
    ):
        user, req_client = requester
        adm_user, adm_client = admin
        code = f"DET-TOD-{uuid.uuid4().hex[:4]}"
        room_data = await _create_room_via_admin(adm_client, code=code)
        room_id = room_data["id"]

        # Create a booking for today via committed session (visible to HTTP client)
        now_tz = datetime.now(TZ)
        today = now_tz.date()
        starts = datetime(today.year, today.month, today.day, 14, 0, tzinfo=TZ)
        ends = datetime(today.year, today.month, today.day, 15, 0, tzinfo=TZ)
        await _insert_booking(test_engine, room_id, adm_user.id, starts, ends)

        resp = await req_client.get(f"/api/v1/rooms/{room_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        today_bkgs = data["today_bookings"]
        assert len(today_bkgs) >= 1
        bk = today_bkgs[0]
        assert "id" in bk
        assert "title" in bk
        assert "starts_at" in bk
        assert "ends_at" in bk
        assert "organizer_name" in bk
        # organizer_name should be the user's full_name
        assert bk["organizer_name"] == adm_user.full_name

    async def test_room_detail_week_bookings_excludes_past_beyond_today(
        self, requester, admin, test_engine
    ):
        user, req_client = requester
        adm_user, adm_client = admin
        code = f"DET-WK-{uuid.uuid4().hex[:4]}"
        room_data = await _create_room_via_admin(adm_client, code=code)
        room_id = room_data["id"]

        now_tz = datetime.now(TZ)
        today = now_tz.date()
        # booking 3 days in the future (within 7-day window)
        future_day = today + timedelta(days=3)
        starts = datetime(future_day.year, future_day.month, future_day.day, 10, 0, tzinfo=TZ)
        ends = datetime(future_day.year, future_day.month, future_day.day, 11, 0, tzinfo=TZ)
        await _insert_booking(test_engine, room_id, adm_user.id, starts, ends)

        resp = await req_client.get(f"/api/v1/rooms/{room_id}")
        assert resp.status_code == 200
        data = resp.json()
        week_bkgs = data["week_bookings"]
        assert len(week_bkgs) >= 1

    async def test_room_detail_unauthenticated_returns_401_or_403(self, client):
        fake = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/rooms/{fake}")
        assert resp.status_code in (401, 403), resp.text


class TestGetRoomsAvailability:
    """GET /api/v1/rooms/availability — rooms with no confirmed overlap."""

    async def test_availability_requires_starts_at_and_ends_at(self, requester):
        user, req_client = requester
        resp = await req_client.get("/api/v1/rooms/availability")
        assert resp.status_code == 422, resp.text

    async def test_availability_returns_rooms_without_overlap(
        self, requester, admin, test_engine
    ):
        user, req_client = requester
        adm_user, adm_client = admin
        code_free = f"AVAIL-F-{uuid.uuid4().hex[:4]}"
        code_busy = f"AVAIL-B-{uuid.uuid4().hex[:4]}"
        await _create_room_via_admin(adm_client, code=code_free)
        busy_room = await _create_room_via_admin(adm_client, code=code_busy)

        # Book busy_room for 14:00–15:00 tomorrow via committed session
        tomorrow = datetime.now(TZ).date() + timedelta(days=1)
        b_start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 14, 0, tzinfo=TZ)
        b_end = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 15, 0, tzinfo=TZ)
        await _insert_booking(test_engine, busy_room["id"], adm_user.id, b_start, b_end)

        # Query availability for that same window
        resp = await req_client.get(
            "/api/v1/rooms/availability",
            params={
                "starts_at": b_start.isoformat(),
                "ends_at": b_end.isoformat(),
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        codes = [r["code"] for r in data]
        assert code_free in codes
        assert code_busy not in codes

    async def test_availability_touching_edges_are_not_conflicting(
        self, requester, admin, test_engine
    ):
        """Booking ends exactly when search window starts → no conflict (touching allowed)."""
        user, req_client = requester
        adm_user, adm_client = admin
        code = f"AVAIL-EDGE-{uuid.uuid4().hex[:4]}"
        room = await _create_room_via_admin(adm_client, code=code)

        tomorrow = datetime.now(TZ).date() + timedelta(days=1)
        # Existing booking: 13:00–14:00
        b_start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 13, 0, tzinfo=TZ)
        b_end = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 14, 0, tzinfo=TZ)
        await _insert_booking(test_engine, room["id"], adm_user.id, b_start, b_end)

        # Search window: 14:00–15:00 (starts exactly when booking ends)
        resp = await req_client.get(
            "/api/v1/rooms/availability",
            params={
                "starts_at": b_end.isoformat(),
                "ends_at": (b_end + timedelta(hours=1)).isoformat(),
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        codes = [r["code"] for r in data]
        # room should appear as available (no overlap)
        assert code in codes

    async def test_availability_cancelled_booking_does_not_block(
        self, requester, admin, test_engine
    ):
        """Cancelled bookings don't block availability."""
        user, req_client = requester
        adm_user, adm_client = admin
        code = f"AVAIL-CANC-{uuid.uuid4().hex[:4]}"
        room = await _create_room_via_admin(adm_client, code=code)

        tomorrow = datetime.now(TZ).date() + timedelta(days=1)
        b_start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 14, 0, tzinfo=TZ)
        b_end = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 15, 0, tzinfo=TZ)
        await _insert_booking(test_engine, room["id"], adm_user.id, b_start, b_end, status="cancelled")

        resp = await req_client.get(
            "/api/v1/rooms/availability",
            params={
                "starts_at": b_start.isoformat(),
                "ends_at": b_end.isoformat(),
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        codes = [r["code"] for r in data]
        assert code in codes

    async def test_availability_unauthenticated_returns_401_or_403(self, client):
        resp = await client.get(
            "/api/v1/rooms/availability",
            params={
                "starts_at": "2026-01-01T14:00:00+00:00",
                "ends_at": "2026-01-01T15:00:00+00:00",
            },
        )
        assert resp.status_code in (401, 403), resp.text

    async def test_availability_route_before_id_route(self, requester):
        """Ensure /rooms/availability is not captured by /rooms/{id}."""
        user, req_client = requester
        # Without starts_at/ends_at it should be 422 (param validation), not 404
        resp = await req_client.get("/api/v1/rooms/availability")
        # 422 = FastAPI param validation (correct), 404 = caught by /{id} (wrong)
        assert resp.status_code == 422, f"Expected 422 (param error), got {resp.status_code}"
