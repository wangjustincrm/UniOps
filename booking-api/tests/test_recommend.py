"""TDD tests for recommend.py — conflict precheck + suggestion engine.

Step 1 (RED): Write all tests FIRST so they fail before implementation exists.
Step 2 (GREEN): Implement app/services/recommend.py and app/api/v1/precheck.py.

Test coverage:
  - find_conflicts: touching edges NOT a conflict; confirmed-only; exclude_booking_ids
  - suggest: ranking order (rank0 same-floor+capacity-band, rank1 adjacent-floor,
             rank2 rest; disabled/maintenance excluded); cap at 5 alternatives;
             nearest_slots trimming (skip gaps shorter than duration; pick up to 3
             closest to requested start)
  - POST /bookings/precheck: 404 unknown room; 422 inverted window; conflicts list;
             suggestions non-null iff conflicts; series field accepted with
             occurrence_conflicts=[] always
"""
from __future__ import annotations

import types
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("America/Toronto")


# ─────────────────────────────────────────────────────────────────────────────
# Duck-typed helpers (no DB, no ORM session needed for pure-function tests)
# ─────────────────────────────────────────────────────────────────────────────

def _room(**kwargs) -> types.SimpleNamespace:
    defaults = dict(
        id=uuid.uuid4(),
        name="Test Room",
        code=f"TR-{uuid.uuid4().hex[:4]}",
        campus="Main",
        building="HQ",
        floor="2",
        area="East",
        capacity=10,
        equipment=[],
        room_type="standard",
        status="available",
        image_file_ids=[],
        open_time_start=time(8, 0),
        open_time_end=time(20, 0),
        advance_booking_days=None,
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
    booking_id: uuid.UUID | None = None,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=booking_id or uuid.uuid4(),
        room_id=room_id,
        title="Test Meeting",
        description=None,
        organizer_id=uuid.uuid4(),
        organizer_name="Test User",
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


def _dt(h: int, m: int = 0, day_offset: int = 1) -> datetime:
    """Return a tz-aware datetime in America/Toronto for today + day_offset."""
    d = datetime.now(TZ).date() + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: find_conflicts — pure DB-backed function tested via integration
# ─────────────────────────────────────────────────────────────────────────────

# Import will fail until recommend.py is created — correct RED behaviour.
from app.services.recommend import find_conflicts, suggest


class TestFindConflictsTouchingEdges:
    """Touching edges (booking.ends_at == query.starts_at) must NOT be a conflict."""

    async def test_touching_edge_end_equals_query_start_no_conflict(
        self, db_session
    ):
        """Booking 13:00–14:00 vs query 14:00–15:00: touching, not a conflict."""
        room = _room()
        # Insert room via ORM
        from app.models.room import MeetingRoom
        from app.models.booking import Booking
        db_room = MeetingRoom(
            id=room.id,
            name=room.name,
            code=room.code,
            campus=room.campus,
            building=room.building,
            floor=room.floor,
            area=room.area,
            capacity=room.capacity,
            equipment=room.equipment,
            status=room.status,
        )
        db_session.add(db_room)
        await db_session.flush()

        existing = Booking(
            room_id=room.id,
            title="Earlier Meeting",
            organizer_id=uuid.uuid4(),
            attendee_ids=[],
            starts_at=_dt(13, 0),
            ends_at=_dt(14, 0),
            status="confirmed",
            calendar_uid=f"touch-{uuid.uuid4()}@test",
        )
        db_session.add(existing)
        await db_session.flush()

        conflicts = await find_conflicts(
            db_session, room.id, _dt(14, 0), _dt(15, 0)
        )
        assert conflicts == [], (
            "Booking ending exactly at query start must NOT be a conflict"
        )

    async def test_touching_edge_query_end_equals_booking_start_no_conflict(
        self, db_session
    ):
        """Query 14:00–15:00 vs booking 15:00–16:00: touching, not a conflict."""
        room = _room()
        from app.models.room import MeetingRoom
        from app.models.booking import Booking
        db_room = MeetingRoom(
            id=room.id,
            name=room.name,
            code=room.code,
            capacity=room.capacity,
            equipment=room.equipment,
            status=room.status,
        )
        db_session.add(db_room)
        await db_session.flush()

        existing = Booking(
            room_id=room.id,
            title="Later Meeting",
            organizer_id=uuid.uuid4(),
            attendee_ids=[],
            starts_at=_dt(15, 0),
            ends_at=_dt(16, 0),
            status="confirmed",
            calendar_uid=f"touch2-{uuid.uuid4()}@test",
        )
        db_session.add(existing)
        await db_session.flush()

        conflicts = await find_conflicts(
            db_session, room.id, _dt(14, 0), _dt(15, 0)
        )
        assert conflicts == [], (
            "Booking starting exactly at query end must NOT be a conflict"
        )

    async def test_overlapping_booking_is_a_conflict(self, db_session):
        """Booking 13:00–15:00 vs query 14:00–16:00: clear overlap."""
        room = _room()
        from app.models.room import MeetingRoom
        from app.models.booking import Booking
        db_room = MeetingRoom(
            id=room.id,
            name=room.name,
            code=room.code,
            capacity=room.capacity,
            equipment=room.equipment,
            status=room.status,
        )
        db_session.add(db_room)
        await db_session.flush()

        existing = Booking(
            room_id=room.id,
            title="Overlapping Meeting",
            organizer_id=uuid.uuid4(),
            attendee_ids=[],
            starts_at=_dt(13, 0),
            ends_at=_dt(15, 0),
            status="confirmed",
            calendar_uid=f"overlap-{uuid.uuid4()}@test",
        )
        db_session.add(existing)
        await db_session.flush()

        conflicts = await find_conflicts(
            db_session, room.id, _dt(14, 0), _dt(16, 0)
        )
        assert len(conflicts) == 1

    async def test_cancelled_booking_not_a_conflict(self, db_session):
        """Cancelled bookings in the overlap window must not be returned."""
        room = _room()
        from app.models.room import MeetingRoom
        from app.models.booking import Booking
        db_room = MeetingRoom(
            id=room.id,
            name=room.name,
            code=room.code,
            capacity=room.capacity,
            equipment=room.equipment,
            status=room.status,
        )
        db_session.add(db_room)
        await db_session.flush()

        existing = Booking(
            room_id=room.id,
            title="Cancelled Meeting",
            organizer_id=uuid.uuid4(),
            attendee_ids=[],
            starts_at=_dt(14, 0),
            ends_at=_dt(15, 0),
            status="cancelled",
            calendar_uid=f"canc-{uuid.uuid4()}@test",
        )
        db_session.add(existing)
        await db_session.flush()

        conflicts = await find_conflicts(
            db_session, room.id, _dt(14, 0), _dt(15, 0)
        )
        assert conflicts == []

    async def test_exclude_booking_ids_removes_specific_bookings(self, db_session):
        """Bookings in exclude_booking_ids must be omitted even if overlapping."""
        room = _room()
        from app.models.room import MeetingRoom
        from app.models.booking import Booking
        db_room = MeetingRoom(
            id=room.id,
            name=room.name,
            code=room.code,
            capacity=room.capacity,
            equipment=room.equipment,
            status=room.status,
        )
        db_session.add(db_room)
        await db_session.flush()

        bid = uuid.uuid4()
        existing = Booking(
            id=bid,
            room_id=room.id,
            title="Self Meeting",
            organizer_id=uuid.uuid4(),
            attendee_ids=[],
            starts_at=_dt(14, 0),
            ends_at=_dt(15, 0),
            status="confirmed",
            calendar_uid=f"excl-{uuid.uuid4()}@test",
        )
        db_session.add(existing)
        await db_session.flush()

        conflicts = await find_conflicts(
            db_session, room.id, _dt(14, 0), _dt(15, 0), exclude_booking_ids={bid}
        )
        assert conflicts == []


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: suggest — ranking + nearest_slots (pure / minimal DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestSuggestRanking:
    """
    Ranking order (PRD 9.4.3):
      rank 0 — same floor AND capacity in band [needed, 2*needed]
      rank 1 — same area OR adjacent floor (floor distance == 1)
      rank 2 — everything else; sub-sorted by (capacity_delta, |cap - needed|, missing_equipment)
    Disabled / maintenance rooms must never appear.
    Cap at 5 alternatives.
    """

    async def test_ranking_order_with_four_rooms(self, db_session):
        """
        Setup (using unique floor 77/76/99 to avoid collision with other test rooms):
          target:  floor=77, area=UniqueWest77, capacity=10, equipment=["projector"]
          roomA:   floor=77, area=UniqueWest77, capacity=10, equipment=["projector"]  → rank 0
          roomB:   floor=76, area=UniqueEast76, capacity=6, equipment=[]              → rank 1 (adjacent floor)
          roomC:   floor=99, area=UniqueNorth99, capacity=50, equipment=[]            → rank 2
          roomD:   floor=77, area=UniqueWest77, capacity=8, status=disabled           → excluded

        We verify relative ordering by calling suggest without cap (bypass via direct
        _rank_candidate check on our rooms) and also validate the global suggest result
        never contains roomD and respects rank ordering for whichever of A/B/C appear.

        Since the test DB may have rooms from other tests in the cap-5 list, we verify
        the rank key ordering directly via _rank_candidate, and check roomD is excluded.
        """
        from app.models.room import MeetingRoom
        from app.services.recommend import _rank_candidate

        # Use floor values unlikely to conflict with real tests (77, 76, 99)
        unique_suffix = uuid.uuid4().hex[:6]
        unique_area_base = f"UniqueArea{unique_suffix}"

        def _mk_room(**kwargs):
            r = dict(
                id=uuid.uuid4(),
                name="RankTestRoom",
                code=f"RTR-{uuid.uuid4().hex[:6]}",
                campus="RankCampus",
                building="RankBldg",
                floor="77",
                area=unique_area_base,
                capacity=10,
                equipment=[],
                room_type="standard",
                status="available",
                image_file_ids=[],
                open_time_start=time(8, 0),
                open_time_end=time(20, 0),
                advance_booking_days=None,
                owner_department=None,
                notes=None,
            )
            r.update(kwargs)
            return MeetingRoom(**r)

        target_room = _mk_room(floor="77", area=unique_area_base, capacity=10,
                               equipment=["projector"], code=f"TGT-{uuid.uuid4().hex[:6]}")
        room_a = _mk_room(floor="77", area=unique_area_base, capacity=10,
                           equipment=["projector"], code=f"RA-{uuid.uuid4().hex[:6]}")
        room_b = _mk_room(floor="76", area=f"Adj{unique_area_base}", capacity=6,
                           equipment=[], code=f"RB-{uuid.uuid4().hex[:6]}")
        room_c = _mk_room(floor="99", area=f"Far{unique_area_base}", capacity=50,
                           equipment=[], code=f"RC-{uuid.uuid4().hex[:6]}")
        room_d = _mk_room(floor="77", area=unique_area_base, capacity=8,
                           equipment=[], status="disabled", code=f"RD-{uuid.uuid4().hex[:6]}")

        for r in [target_room, room_a, room_b, room_c, room_d]:
            db_session.add(r)
        await db_session.flush()

        starts_at = _dt(14, 0)
        ends_at = _dt(15, 0)

        cfg_rules = {
            "slot_minutes": 15,
            "default_open_start": "08:00",
            "default_open_end": "20:00",
        }

        result = await suggest(
            db_session,
            room=target_room,
            starts_at=starts_at,
            ends_at=ends_at,
            attendee_count=10,
            equipment=["projector"],
            cfg_rules=cfg_rules,
        )

        alt_ids = [r.id for r in result["alternative_rooms"]]

        # roomD must always be excluded (disabled)
        assert room_d.id not in alt_ids, "Disabled room must not appear in alternatives"

        # Verify ranking key ordering via _rank_candidate directly
        # (the suggest cap-5 may exclude lower-ranked rooms if there are many others,
        # but rank ordering must be correct for all rooms that do appear)
        rank_a = _rank_candidate(room_a, target_room, 10, ["projector"])
        rank_b = _rank_candidate(room_b, target_room, 10, ["projector"])
        rank_c = _rank_candidate(room_c, target_room, 10, ["projector"])

        assert rank_a[0] == 0, f"roomA must have rank 0, got {rank_a}"
        assert rank_b[0] == 1, f"roomB must have rank 1, got {rank_b}"
        assert rank_c[0] == 2, f"roomC must have rank 2, got {rank_c}"
        assert rank_a < rank_b, "rank-0 key must sort before rank-1 key"
        assert rank_b < rank_c, "rank-1 key must sort before rank-2 key"

        # If roomA appears in results, it must come before roomB
        if room_a.id in alt_ids and room_b.id in alt_ids:
            idx_a = alt_ids.index(room_a.id)
            idx_b = alt_ids.index(room_b.id)
            assert idx_a < idx_b, f"rank-0 room (idx {idx_a}) must precede rank-1 room (idx {idx_b})"

        # If roomB appears in results, it must come before roomC
        if room_b.id in alt_ids and room_c.id in alt_ids:
            idx_b = alt_ids.index(room_b.id)
            idx_c = alt_ids.index(room_c.id)
            assert idx_b < idx_c, f"rank-1 room (idx {idx_b}) must precede rank-2 room (idx {idx_c})"

    async def test_disabled_and_maintenance_rooms_excluded(self, db_session):
        """Rooms with status disabled or maintenance never appear in alternatives."""
        from app.models.room import MeetingRoom

        def _mk_room(**kwargs):
            r = dict(
                id=uuid.uuid4(),
                name="Room",
                code=f"R-{uuid.uuid4().hex[:4]}",
                campus="Main",
                building="HQ",
                floor="3",
                area="West",
                capacity=10,
                equipment=[],
                room_type="standard",
                status="available",
                image_file_ids=[],
                open_time_start=time(8, 0),
                open_time_end=time(20, 0),
                advance_booking_days=None,
                owner_department=None,
                notes=None,
            )
            r.update(kwargs)
            return MeetingRoom(**r)

        target_room = _mk_room(code=f"TGT2-{uuid.uuid4().hex[:4]}")
        disabled_room = _mk_room(status="disabled", code=f"DIS-{uuid.uuid4().hex[:4]}")
        maint_room = _mk_room(status="maintenance", code=f"MNT-{uuid.uuid4().hex[:4]}")

        for r in [target_room, disabled_room, maint_room]:
            db_session.add(r)
        await db_session.flush()

        cfg_rules = {"slot_minutes": 15, "default_open_start": "08:00", "default_open_end": "20:00"}
        result = await suggest(
            db_session,
            room=target_room,
            starts_at=_dt(14, 0),
            ends_at=_dt(15, 0),
            attendee_count=None,
            equipment=[],
            cfg_rules=cfg_rules,
        )
        alt_ids = [r.id for r in result["alternative_rooms"]]
        assert disabled_room.id not in alt_ids
        assert maint_room.id not in alt_ids

    async def test_alternatives_capped_at_five(self, db_session):
        """Even with many candidate rooms, alternative_rooms is capped at 5."""
        from app.models.room import MeetingRoom

        def _mk_room(**kwargs):
            r = dict(
                id=uuid.uuid4(),
                name="Room",
                code=f"R-{uuid.uuid4().hex[:4]}",
                campus="Main",
                building="HQ",
                floor="9",
                area="Far",
                capacity=10,
                equipment=[],
                room_type="standard",
                status="available",
                image_file_ids=[],
                open_time_start=time(8, 0),
                open_time_end=time(20, 0),
                advance_booking_days=None,
                owner_department=None,
                notes=None,
            )
            r.update(kwargs)
            return MeetingRoom(**r)

        target_room = _mk_room(floor="1", area="Close", code=f"TGTCAP-{uuid.uuid4().hex[:4]}")
        candidates = [_mk_room() for _ in range(8)]

        for r in [target_room] + candidates:
            db_session.add(r)
        await db_session.flush()

        cfg_rules = {"slot_minutes": 15, "default_open_start": "08:00", "default_open_end": "20:00"}
        result = await suggest(
            db_session,
            room=target_room,
            starts_at=_dt(14, 0),
            ends_at=_dt(15, 0),
            attendee_count=None,
            equipment=[],
            cfg_rules=cfg_rules,
        )
        assert len(result["alternative_rooms"]) <= 5

    async def test_busy_room_excluded_from_alternatives(self, db_session):
        """A room that has a confirmed booking in the window must not appear as alternative.

        We use a unique campus so the target and test rooms are the only rooms on that
        campus, then call suggest and assert:
          1. busy_room.id never appears (it's booked)
          2. All rooms in alt_ids are available (not busy_room)
        We do NOT assert free_room is in the top-5, because many other committed
        rooms from previous tests may outrank it in the cap-5.
        """
        from app.models.room import MeetingRoom
        from app.models.booking import Booking

        unique_suffix = uuid.uuid4().hex[:6]

        def _mk_room(**kwargs):
            r = dict(
                id=uuid.uuid4(),
                name="BusyTestRoom",
                code=f"BTR-{uuid.uuid4().hex[:6]}",
                campus=f"BusyCampus{unique_suffix}",
                building="HQ",
                floor="3",
                area="West",
                capacity=10,
                equipment=[],
                room_type="standard",
                status="available",
                image_file_ids=[],
                open_time_start=time(8, 0),
                open_time_end=time(20, 0),
                advance_booking_days=None,
                owner_department=None,
                notes=None,
            )
            r.update(kwargs)
            return MeetingRoom(**r)

        target_room = _mk_room(floor="1", code=f"TGTBSY-{uuid.uuid4().hex[:6]}")
        busy_room = _mk_room(floor="3", code=f"BSY-{uuid.uuid4().hex[:6]}")
        free_room = _mk_room(floor="3", code=f"FREE-{uuid.uuid4().hex[:6]}")

        for r in [target_room, busy_room, free_room]:
            db_session.add(r)
        await db_session.flush()

        # Book the busy_room in the requested window
        starts_at = _dt(14, 0)
        ends_at = _dt(15, 0)
        booking = Booking(
            room_id=busy_room.id,
            title="Blocker",
            organizer_id=uuid.uuid4(),
            attendee_ids=[],
            starts_at=starts_at,
            ends_at=ends_at,
            status="confirmed",
            calendar_uid=f"bsy-{uuid.uuid4()}@test",
        )
        db_session.add(booking)
        await db_session.flush()

        cfg_rules = {"slot_minutes": 15, "default_open_start": "08:00", "default_open_end": "20:00"}
        result = await suggest(
            db_session,
            room=target_room,
            starts_at=starts_at,
            ends_at=ends_at,
            attendee_count=None,
            equipment=[],
            cfg_rules=cfg_rules,
        )
        alt_ids = [r.id for r in result["alternative_rooms"]]

        # The core invariant: busy room must NEVER appear, regardless of cap
        assert busy_room.id not in alt_ids, "Busy room must not appear as alternative"


class TestSuggestNearestSlots:
    """nearest_slots: up to 3 gaps closest to requested start, trimmed to duration."""

    async def test_nearest_slots_skips_gaps_shorter_than_duration(self, db_session):
        """A free gap shorter than the requested duration must be excluded."""
        from app.models.room import MeetingRoom
        from app.models.booking import Booking

        target_room = MeetingRoom(
            id=uuid.uuid4(),
            name="Target",
            code=f"NS-TGT-{uuid.uuid4().hex[:4]}",
            capacity=10,
            equipment=[],
            status="available",
            open_time_start=time(8, 0),
            open_time_end=time(20, 0),
        )
        db_session.add(target_room)
        await db_session.flush()

        # Create bookings that leave a 10-min gap (too short for 60-min meeting)
        # Gap: 09:10–09:20 (10 min). Requested duration = 60 min.
        day = _dt(9, 0).date()
        b1 = Booking(
            room_id=target_room.id,
            title="B1",
            organizer_id=uuid.uuid4(),
            attendee_ids=[],
            starts_at=datetime(day.year, day.month, day.day, 8, 0, tzinfo=TZ),
            ends_at=datetime(day.year, day.month, day.day, 9, 10, tzinfo=TZ),
            status="confirmed",
            calendar_uid=f"ns1-{uuid.uuid4()}@test",
        )
        b2 = Booking(
            room_id=target_room.id,
            title="B2",
            organizer_id=uuid.uuid4(),
            attendee_ids=[],
            starts_at=datetime(day.year, day.month, day.day, 9, 20, tzinfo=TZ),
            ends_at=datetime(day.year, day.month, day.day, 12, 0, tzinfo=TZ),
            status="confirmed",
            calendar_uid=f"ns2-{uuid.uuid4()}@test",
        )
        db_session.add(b1)
        db_session.add(b2)
        await db_session.flush()

        starts_at = datetime(day.year, day.month, day.day, 9, 0, tzinfo=TZ)
        ends_at = starts_at + timedelta(hours=1)

        cfg_rules = {"slot_minutes": 15, "default_open_start": "08:00", "default_open_end": "20:00"}
        result = await suggest(
            db_session,
            room=target_room,
            starts_at=starts_at,
            ends_at=ends_at,
            attendee_count=None,
            equipment=[],
            cfg_rules=cfg_rules,
        )

        # All nearest_slots must have duration >= requested duration (60 min)
        duration = timedelta(hours=1)
        for slot in result["nearest_slots"]:
            slot_dur = slot["ends_at"] - slot["starts_at"]
            assert slot_dur >= duration, (
                f"Slot duration {slot_dur} shorter than requested {duration}"
            )

    async def test_nearest_slots_at_most_three(self, db_session):
        """Even if many gaps exist, nearest_slots returns at most 3."""
        from app.models.room import MeetingRoom

        target_room = MeetingRoom(
            id=uuid.uuid4(),
            name="Target Many Gaps",
            code=f"NS-MG-{uuid.uuid4().hex[:4]}",
            capacity=10,
            equipment=[],
            status="available",
            open_time_start=time(8, 0),
            open_time_end=time(23, 0),  # long open window
        )
        db_session.add(target_room)
        await db_session.flush()

        # No bookings — whole day is free → free_slots returns one big gap
        # The function should return at most 3 slots
        starts_at = _dt(10, 0)
        ends_at = _dt(11, 0)

        cfg_rules = {"slot_minutes": 15, "default_open_start": "08:00", "default_open_end": "23:00"}
        result = await suggest(
            db_session,
            room=target_room,
            starts_at=starts_at,
            ends_at=ends_at,
            attendee_count=None,
            equipment=[],
            cfg_rules=cfg_rules,
        )
        assert len(result["nearest_slots"]) <= 3

    async def test_nearest_slots_trimmed_to_duration(self, db_session):
        """Each returned slot must span exactly the requested duration (not the whole gap)."""
        from app.models.room import MeetingRoom

        target_room = MeetingRoom(
            id=uuid.uuid4(),
            name="Target Trim",
            code=f"NS-TR-{uuid.uuid4().hex[:4]}",
            capacity=10,
            equipment=[],
            status="available",
            open_time_start=time(8, 0),
            open_time_end=time(20, 0),
        )
        db_session.add(target_room)
        await db_session.flush()

        starts_at = _dt(14, 0)
        ends_at = _dt(15, 0)  # 60-min meeting
        duration = ends_at - starts_at

        cfg_rules = {"slot_minutes": 15, "default_open_start": "08:00", "default_open_end": "20:00"}
        result = await suggest(
            db_session,
            room=target_room,
            starts_at=starts_at,
            ends_at=ends_at,
            attendee_count=None,
            equipment=[],
            cfg_rules=cfg_rules,
        )

        for slot in result["nearest_slots"]:
            slot_dur = slot["ends_at"] - slot["starts_at"]
            assert slot_dur == duration, (
                f"Expected slot duration {duration}, got {slot_dur}"
            )

    async def test_nearest_slots_anchored_toward_requested_start(self, db_session):
        """Nearest slots must be anchored toward starts_at, not at gap_start.

        Setup:
          - Two gaps: 08:00–13:00 and 15:00–20:00
          - Requested window: 13:00–14:00 (1 hour) — conflicts with something at 13:00–15:00
          - Conflict booking fills 13:00–15:00, creating the two gaps above.

        Expected nearest slots (anchored at requested start 13:00, duration 1h):
          - Gap 08:00–13:00: feasible range [08:00, 12:00]; anchor toward 13:00 → 12:00–13:00
          - Gap 15:00–20:00: feasible range [15:00, 19:00]; anchor toward 13:00 → 15:00–16:00

        The OLD behavior (anchoring at gap_start) would return 08:00–09:00 and 15:00–16:00.
        The NEW behavior anchors to 12:00–13:00 and 15:00–16:00 — both much closer to 13:00.
        """
        from app.models.room import MeetingRoom
        from app.models.booking import Booking

        day = _dt(0, 0, day_offset=2).date()  # day after tomorrow (avoid today-boundary issues)

        target_room = MeetingRoom(
            id=uuid.uuid4(),
            name="Anchor Test Room",
            code=f"NS-ANC-{uuid.uuid4().hex[:4]}",
            capacity=10,
            equipment=[],
            status="available",
            open_time_start=time(8, 0),
            open_time_end=time(20, 0),
        )
        db_session.add(target_room)
        await db_session.flush()

        # Booking that fills 13:00–15:00, creating gaps 08:00–13:00 and 15:00–20:00
        conflict_booking = Booking(
            room_id=target_room.id,
            title="Blocker",
            organizer_id=uuid.uuid4(),
            attendee_ids=[],
            starts_at=datetime(day.year, day.month, day.day, 13, 0, tzinfo=TZ),
            ends_at=datetime(day.year, day.month, day.day, 15, 0, tzinfo=TZ),
            status="confirmed",
            calendar_uid=f"anc-{uuid.uuid4()}@test",
        )
        db_session.add(conflict_booking)
        await db_session.flush()

        # Request 13:00–14:00 (conflicts with the 13:00–15:00 booking)
        starts_at = datetime(day.year, day.month, day.day, 13, 0, tzinfo=TZ)
        ends_at = datetime(day.year, day.month, day.day, 14, 0, tzinfo=TZ)
        duration = ends_at - starts_at  # 1 hour

        cfg_rules = {"slot_minutes": 15, "default_open_start": "08:00", "default_open_end": "20:00"}
        result = await suggest(
            db_session,
            room=target_room,
            starts_at=starts_at,
            ends_at=ends_at,
            attendee_count=None,
            equipment=[],
            cfg_rules=cfg_rules,
        )

        slots = result["nearest_slots"]
        assert len(slots) >= 1, "Expected at least one nearest slot"

        # All returned slots must span exactly the requested duration
        for slot in slots:
            slot_dur = slot["ends_at"] - slot["starts_at"]
            assert slot_dur == duration, f"Slot duration {slot_dur} != requested {duration}"

        # The first (closest) slot should be anchored near 13:00.
        # Gap 08:00–13:00 → anchored to 12:00–13:00 (distance = 1h from 13:00).
        # Gap 15:00–20:00 → anchored to 15:00–16:00 (distance = 2h from 13:00).
        # Both are better than 08:00–09:00 (distance = 5h).
        slot_starts = [s["starts_at"] for s in slots]
        ref = starts_at  # 13:00

        # None of the offered slots should start at 08:00 (the old incorrect behavior)
        eight_am = datetime(day.year, day.month, day.day, 8, 0, tzinfo=TZ)
        assert eight_am not in slot_starts, (
            "Slot anchored at gap_start 08:00 must not appear; "
            "expected anchor near 12:00 (inside 08:00–13:00 gap)"
        )

        # The closest slot must be no farther than 2h from requested start
        closest_dist = min(abs((s - ref).total_seconds()) for s in slot_starts)
        assert closest_dist <= 2 * 3600, (
            f"Closest slot is {closest_dist/3600:.1f}h from requested start — "
            "expected ≤2h (anchored toward 13:00)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: POST /bookings/precheck — HTTP integration tests
# ─────────────────────────────────────────────────────────────────────────────

from tests.conftest import make_user, make_token, authed_client


async def _create_room_via_admin(admin_client, **overrides) -> dict:
    payload = {
        "name": "Precheck Room",
        "code": f"PCR-{uuid.uuid4().hex[:6]}",
        "campus": "Main",
        "building": "HQ",
        "floor": "2",
        "area": "East",
        "capacity": 10,
        "equipment": [],
        "room_type": "standard",
        "open_time_start": "08:00:00",
        "open_time_end": "20:00:00",
    }
    payload.update(overrides)
    resp = await admin_client.post("/api/v1/admin/rooms", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _insert_booking(db_session, room_id, starts_at, ends_at, status="confirmed"):
    """Insert a booking via the per-test db_session (no commit needed).

    The HTTP client fixture is bound to the same db_session connection so it
    sees these rows immediately without any commit.  All rows are rolled back
    on teardown — no cross-test pollution.
    """
    from app.models.booking import Booking as BookingModel
    b = BookingModel(
        room_id=uuid.UUID(room_id) if isinstance(room_id, str) else room_id,
        title="Precheck Conflict",
        organizer_id=uuid.uuid4(),
        attendee_ids=[],
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
        calendar_uid=f"pc-{uuid.uuid4()}@test",
    )
    db_session.add(b)
    await db_session.flush()
    return b


class TestPrecheckEndpoint:
    """POST /api/v1/bookings/precheck — conflict precheck."""

    async def test_precheck_404_for_unknown_room(self, requester):
        user, req_client = requester
        fake_room = str(uuid.uuid4())
        tomorrow = datetime.now(TZ).date() + timedelta(days=1)
        starts = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 14, 0, tzinfo=TZ)
        ends = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 15, 0, tzinfo=TZ)
        resp = await req_client.post(
            "/api/v1/bookings/precheck",
            json={
                "room_id": fake_room,
                "starts_at": starts.isoformat(),
                "ends_at": ends.isoformat(),
            },
        )
        assert resp.status_code == 404, resp.text

    async def test_precheck_422_when_ends_at_lte_starts_at(self, requester, admin):
        user, req_client = requester
        adm_user, adm_client = admin
        room = await _create_room_via_admin(adm_client)
        t = datetime.now(TZ).replace(hour=14, minute=0, second=0, microsecond=0)
        resp = await req_client.post(
            "/api/v1/bookings/precheck",
            json={
                "room_id": room["id"],
                "starts_at": t.isoformat(),
                "ends_at": t.isoformat(),  # same = zero length
            },
        )
        assert resp.status_code == 422, resp.text

    async def test_precheck_no_conflicts_returns_empty_conflicts_and_null_suggestions(
        self, requester, admin
    ):
        user, req_client = requester
        adm_user, adm_client = admin
        room = await _create_room_via_admin(adm_client)

        tomorrow = datetime.now(TZ).date() + timedelta(days=1)
        starts = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 14, 0, tzinfo=TZ)
        ends = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 15, 0, tzinfo=TZ)

        resp = await req_client.post(
            "/api/v1/bookings/precheck",
            json={
                "room_id": room["id"],
                "starts_at": starts.isoformat(),
                "ends_at": ends.isoformat(),
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["conflicts"] == []
        assert data["suggestions"] is None, "suggestions must be null when no conflicts"

    async def test_precheck_with_conflict_returns_conflicts_and_suggestions(
        self, requester, admin, db_session
    ):
        user, req_client = requester
        adm_user, adm_client = admin
        room = await _create_room_via_admin(adm_client)

        tomorrow = datetime.now(TZ).date() + timedelta(days=1)
        starts = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 14, 0, tzinfo=TZ)
        ends = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 15, 0, tzinfo=TZ)

        # Insert a confirmed booking via the per-test db_session.
        # The HTTP client is bound to the same session, so it sees this row immediately.
        await _insert_booking(db_session, room["id"], starts, ends)

        resp = await req_client.post(
            "/api/v1/bookings/precheck",
            json={
                "room_id": room["id"],
                "starts_at": starts.isoformat(),
                "ends_at": ends.isoformat(),
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["conflicts"]) >= 1, "Should detect the existing booking as a conflict"
        assert data["suggestions"] is not None, "suggestions must be non-null when conflicts exist"
        assert "nearest_slots" in data["suggestions"]
        assert "alternative_rooms" in data["suggestions"]

    async def test_precheck_conflict_touching_edge_not_a_conflict(
        self, requester, admin, db_session
    ):
        """Existing booking ends exactly when requested window starts → no conflict."""
        user, req_client = requester
        adm_user, adm_client = admin
        room = await _create_room_via_admin(adm_client)

        tomorrow = datetime.now(TZ).date() + timedelta(days=1)
        # Existing: 13:00–14:00
        b_start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 13, 0, tzinfo=TZ)
        b_end = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 14, 0, tzinfo=TZ)
        await _insert_booking(db_session, room["id"], b_start, b_end)

        # Requested: 14:00–15:00
        req_start = b_end
        req_end = req_start + timedelta(hours=1)

        resp = await req_client.post(
            "/api/v1/bookings/precheck",
            json={
                "room_id": room["id"],
                "starts_at": req_start.isoformat(),
                "ends_at": req_end.isoformat(),
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["conflicts"] == [], "Touching edge must not produce a conflict"
        assert data["suggestions"] is None

    async def test_precheck_unauthenticated_returns_401_or_403(self, client, admin):
        adm_user, adm_client = admin
        room = await _create_room_via_admin(adm_client)
        tomorrow = datetime.now(TZ).date() + timedelta(days=1)
        starts = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 14, 0, tzinfo=TZ)
        ends = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 15, 0, tzinfo=TZ)
        resp = await client.post(
            "/api/v1/bookings/precheck",
            json={
                "room_id": room["id"],
                "starts_at": starts.isoformat(),
                "ends_at": ends.isoformat(),
            },
        )
        assert resp.status_code in (401, 403), resp.text

    async def test_precheck_accepts_series_field_and_returns_empty_occurrence_conflicts(
        self, requester, admin
    ):
        """series field is accepted in body; occurrence_conflicts always [] (Task 7 wires expansion)."""
        user, req_client = requester
        adm_user, adm_client = admin
        room = await _create_room_via_admin(adm_client)

        tomorrow = datetime.now(TZ).date() + timedelta(days=1)
        starts = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 10, 0, tzinfo=TZ)
        ends = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 11, 0, tzinfo=TZ)

        resp = await req_client.post(
            "/api/v1/bookings/precheck",
            json={
                "room_id": room["id"],
                "starts_at": starts.isoformat(),
                "ends_at": ends.isoformat(),
                "series": {
                    "freq": "weekly",
                    "interval": 1,
                    "count": 4,
                },
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "occurrence_conflicts" in data
        assert data["occurrence_conflicts"] == [], (
            "Task 7 wires series expansion; for now always []"
        )

    async def test_precheck_response_shape_has_required_fields(
        self, requester, admin
    ):
        user, req_client = requester
        adm_user, adm_client = admin
        room = await _create_room_via_admin(adm_client)

        tomorrow = datetime.now(TZ).date() + timedelta(days=1)
        starts = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 14, 0, tzinfo=TZ)
        ends = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 15, 0, tzinfo=TZ)

        resp = await req_client.post(
            "/api/v1/bookings/precheck",
            json={
                "room_id": room["id"],
                "starts_at": starts.isoformat(),
                "ends_at": ends.isoformat(),
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "conflicts" in data
        assert "occurrence_conflicts" in data
        assert "suggestions" in data
