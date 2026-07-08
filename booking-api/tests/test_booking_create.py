"""TDD tests for POST /bookings and GET /bookings/mine.

Step 1 (RED): All tests written FIRST — must fail before implementation.
Step 2 (GREEN): Implement routes, crud, schemas, recurrence service.

Test coverage (per task-7-brief):
  - happy single booking → 201, sync_status=pending, audit row exists
  - conflict → 400 + suggestions shape + nothing persisted
  - series occurrence-3 conflict → 400 naming that date, zero rows persisted
  - duration 5h (300 min) → 422 (max is 240 min by default)
  - room in maintenance → 422 "Room is not bookable"
  - unaligned 14:07 start → 422
  - start == end → 422
  - unknown room → 404
  - needs_video_conf=True but room has no video_conf equipment → 422
  - series weekly count=4 → 4 booking rows, shared series_id
  - GET /bookings/mine returns the organizer's bookings
  - advance window: booking too far in future → 422
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import make_token, authed_client, make_user

TZ = ZoneInfo("America/Toronto")

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _dt(h: int, m: int = 0, days_ahead: int = 1) -> datetime:
    """tz-aware datetime in America/Toronto, N days ahead."""
    d = datetime.now(TZ).date() + timedelta(days=days_ahead)
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _create_room(client, *, status: str = "available", equipment: list | None = None, **kwargs) -> dict:
    """Helper: create a room via admin API and return the JSON response."""
    payload = {
        "name": f"Test Room {uuid.uuid4().hex[:6]}",
        "code": f"TR-{uuid.uuid4().hex[:6]}",
        "campus": "Main",
        "building": "HQ",
        "floor": "2",
        "area": "East",
        "capacity": 10,
        "equipment": equipment or [],
        "room_type": "standard",
        "open_time_start": "08:00:00",
        "open_time_end": "20:00:00",
    }
    payload.update(kwargs)
    resp = await client.post("/api/v1/admin/rooms", json=payload)
    assert resp.status_code == 201, f"Room creation failed: {resp.text}"
    room = resp.json()
    if status != "available":
        sr = await client.post(
            f"/api/v1/admin/rooms/{room['id']}/status",
            json={"status": status, "notes": "test"},
        )
        assert sr.status_code == 200, f"Status change failed: {sr.text}"
    return room


async def _insert_booking_raw(db_session, room_id, starts_at: datetime, ends_at: datetime):
    """Insert a confirmed booking via the per-test db_session (no commit)."""
    from app.models.booking import Booking as BookingModel
    b = BookingModel(
        room_id=uuid.UUID(room_id) if isinstance(room_id, str) else room_id,
        title="Conflict Blocker",
        organizer_id=uuid.uuid4(),
        attendee_ids=[],
        starts_at=starts_at,
        ends_at=ends_at,
        status="confirmed",
        calendar_uid=f"blk-{uuid.uuid4()}@test",
    )
    db_session.add(b)
    await db_session.flush()
    return b


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Single booking happy path
# ─────────────────────────────────────────────────────────────────────────────

class TestSingleBookingHappyPath:
    async def test_single_booking_returns_201(self, requester, admin):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        starts = _dt(10, 0)
        ends = _dt(11, 0)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Team Standup",
            "starts_at": _iso(starts),
            "ends_at": _iso(ends),
        })
        assert resp.status_code == 201, resp.text

    async def test_single_booking_response_shape(self, requester, admin):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        starts = _dt(10, 0)
        ends = _dt(11, 0)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Team Standup",
            "starts_at": _iso(starts),
            "ends_at": _iso(ends),
        })
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert "bookings" in data
        assert isinstance(data["bookings"], list)
        assert len(data["bookings"]) == 1
        assert "series_id" in data
        assert data["series_id"] is None  # single booking has no series

    async def test_single_booking_sync_status_is_pending(self, requester, admin):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        starts = _dt(10, 0)
        ends = _dt(11, 0)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Sync Test",
            "starts_at": _iso(starts),
            "ends_at": _iso(ends),
        })
        assert resp.status_code == 201, resp.text
        booking = resp.json()["bookings"][0]
        assert booking["sync_status"] == "pending"

    async def test_single_booking_audit_row_exists(self, requester, admin, db_session):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        starts = _dt(10, 0)
        ends = _dt(11, 0)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Audit Test",
            "starts_at": _iso(starts),
            "ends_at": _iso(ends),
        })
        assert resp.status_code == 201, resp.text
        booking_id = resp.json()["bookings"][0]["id"]

        # Verify audit row via db_session
        from sqlalchemy import select
        from app.models.audit import BookingAuditLog
        result = await db_session.execute(
            select(BookingAuditLog).where(
                BookingAuditLog.booking_id == uuid.UUID(booking_id),
                BookingAuditLog.action == "create",
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None, "Expected audit log row with action='create'"
        assert audit.actor_id == user.id

    async def test_single_booking_bookingout_fields(self, requester, admin):
        """BookingOut must include room_name, room_code, status, series_id, rrule, sync_status."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Field Check",
            "description": "A description",
            "starts_at": _iso(_dt(10, 0)),
            "ends_at": _iso(_dt(11, 0)),
            "attendee_ids": [],
        })
        assert resp.status_code == 201, resp.text
        b = resp.json()["bookings"][0]
        assert b["status"] == "confirmed"
        assert b["room_id"] == room["id"]
        assert b["room_name"] == room["name"]
        assert b["room_code"] == room["code"]
        assert "series_id" in b
        assert b["rrule"] is None
        assert b["sync_status"] == "pending"
        assert b["description"] == "A description"
        assert b["attendee_ids"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: Validation errors (422)
# ─────────────────────────────────────────────────────────────────────────────

class TestBookingValidation422:
    async def test_start_equals_end_returns_422(self, requester, admin):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)
        t = _dt(10, 0)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Bad",
            "starts_at": _iso(t),
            "ends_at": _iso(t),
        })
        assert resp.status_code == 422, resp.text

    async def test_end_before_start_returns_422(self, requester, admin):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Bad",
            "starts_at": _iso(_dt(11, 0)),
            "ends_at": _iso(_dt(10, 0)),
        })
        assert resp.status_code == 422, resp.text

    async def test_unaligned_14_07_start_returns_422(self, requester, admin):
        """14:07 start violates the 15-min grid rule."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Misaligned",
            "starts_at": _iso(_dt(14, 7)),
            "ends_at": _iso(_dt(15, 7)),
        })
        assert resp.status_code == 422, resp.text

    async def test_duration_5_hours_returns_422(self, requester, admin):
        """5 hours = 300 min exceeds default max_duration_minutes=240."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Too Long",
            "starts_at": _iso(_dt(9, 0)),
            "ends_at": _iso(_dt(14, 0)),  # 9:00 to 14:00 = 5h
        })
        assert resp.status_code == 422, resp.text

    async def test_room_in_maintenance_returns_422(self, requester, admin):
        """Room with status=maintenance must return 422 'Room is not bookable'."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client, status="maintenance")
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Blocked",
            "starts_at": _iso(_dt(10, 0)),
            "ends_at": _iso(_dt(11, 0)),
        })
        assert resp.status_code == 422, resp.text
        assert "not bookable" in resp.text.lower() or "bookable" in resp.text.lower()

    async def test_room_disabled_returns_422(self, requester, admin):
        """Room with status=disabled must return 422 'Room is not bookable'."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client, status="disabled")
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Blocked",
            "starts_at": _iso(_dt(10, 0)),
            "ends_at": _iso(_dt(11, 0)),
        })
        assert resp.status_code == 422, resp.text

    async def test_unknown_room_returns_404(self, requester):
        user, req_client = requester
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": str(uuid.uuid4()),
            "title": "Ghost Room",
            "starts_at": _iso(_dt(10, 0)),
            "ends_at": _iso(_dt(11, 0)),
        })
        assert resp.status_code == 404, resp.text

    async def test_needs_video_conf_room_without_equipment_returns_422(self, requester, admin):
        """needs_video_conf=True but room has no video_conf equipment → 422."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client, equipment=["projector"])  # no video_conf
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Video Meeting",
            "starts_at": _iso(_dt(10, 0)),
            "ends_at": _iso(_dt(11, 0)),
            "needs_video_conf": True,
        })
        assert resp.status_code == 422, resp.text

    async def test_needs_video_conf_room_with_equipment_returns_201(self, requester, admin):
        """needs_video_conf=True and room has video_conf → 201."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client, equipment=["video_conf"])
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Video Meeting OK",
            "starts_at": _iso(_dt(10, 0)),
            "ends_at": _iso(_dt(11, 0)),
            "needs_video_conf": True,
        })
        assert resp.status_code == 201, resp.text

    async def test_advance_window_too_far_returns_422(self, requester, admin):
        """Booking start beyond now+advance_days → 422."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)
        # default advance_days=30; book 60 days ahead
        far_starts = _dt(10, 0, days_ahead=60)
        far_ends = _dt(11, 0, days_ahead=60)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Too Far",
            "starts_at": _iso(far_starts),
            "ends_at": _iso(far_ends),
        })
        assert resp.status_code == 422, resp.text

    async def test_title_too_long_returns_422(self, requester, admin):
        """Title > 255 chars → 422."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "A" * 256,
            "starts_at": _iso(_dt(10, 0)),
            "ends_at": _iso(_dt(11, 0)),
        })
        assert resp.status_code == 422, resp.text

    async def test_empty_title_returns_422(self, requester, admin):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "",
            "starts_at": _iso(_dt(10, 0)),
            "ends_at": _iso(_dt(11, 0)),
        })
        assert resp.status_code == 422, resp.text

    async def test_outside_open_hours_returns_422(self, requester, admin):
        """Booking starting before room open_time_start → 422."""
        user, req_client = requester
        _, adm_client = admin
        # Room opens at 08:00 (default); book at 06:00–07:00
        room = await _create_room(adm_client)
        d = datetime.now(TZ).date() + timedelta(days=1)
        early_start = datetime(d.year, d.month, d.day, 6, 0, tzinfo=TZ)
        early_end = datetime(d.year, d.month, d.day, 7, 0, tzinfo=TZ)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Too Early",
            "starts_at": _iso(early_start),
            "ends_at": _iso(early_end),
        })
        assert resp.status_code == 422, resp.text

    async def test_below_min_duration_returns_422(self, requester, admin):
        """10-min booking when min_duration_minutes=15 → 422."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)
        starts = _dt(10, 0)
        ends = starts + timedelta(minutes=10)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Too Short",
            "starts_at": _iso(starts),
            "ends_at": _iso(ends),
        })
        assert resp.status_code == 422, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Conflict → 400
# ─────────────────────────────────────────────────────────────────────────────

class TestBookingConflict400:
    async def test_conflict_returns_400(self, requester, admin, db_session):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        starts = _dt(10, 0)
        ends = _dt(11, 0)
        # Insert blocking booking
        await _insert_booking_raw(db_session, room["id"], starts, ends)

        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Conflicting",
            "starts_at": _iso(starts),
            "ends_at": _iso(ends),
        })
        assert resp.status_code == 400, resp.text

    async def test_conflict_response_shape(self, requester, admin, db_session):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        starts = _dt(10, 0)
        ends = _dt(11, 0)
        await _insert_booking_raw(db_session, room["id"], starts, ends)

        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Conflicting",
            "starts_at": _iso(starts),
            "ends_at": _iso(ends),
        })
        assert resp.status_code == 400, resp.text
        data = resp.json()
        assert data["detail"] == "conflict"
        assert "conflicts" in data
        assert len(data["conflicts"]) >= 1
        assert "suggestions" in data
        assert data["suggestions"] is not None
        assert "nearest_slots" in data["suggestions"]
        assert "alternative_rooms" in data["suggestions"]

    async def test_conflict_nothing_persisted(self, requester, admin, db_session):
        """When conflict is detected, no booking row must be inserted."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        starts = _dt(10, 0)
        ends = _dt(11, 0)
        await _insert_booking_raw(db_session, room["id"], starts, ends)

        # Count bookings before
        from sqlalchemy import select, func
        from app.models.booking import Booking as BookingModel
        count_before_result = await db_session.execute(
            select(func.count()).select_from(BookingModel).where(
                BookingModel.room_id == uuid.UUID(room["id"])
            )
        )
        count_before = count_before_result.scalar()

        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Conflicting",
            "starts_at": _iso(starts),
            "ends_at": _iso(ends),
        })
        assert resp.status_code == 400, resp.text

        count_after_result = await db_session.execute(
            select(func.count()).select_from(BookingModel).where(
                BookingModel.room_id == uuid.UUID(room["id"])
            )
        )
        count_after = count_after_result.scalar()
        assert count_before == count_after, (
            "No booking row must be persisted when conflict is detected"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: Recurring series
# ─────────────────────────────────────────────────────────────────────────────

class TestRecurringSeries:
    async def test_weekly_count_4_creates_4_rows(self, requester, admin, db_session):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        starts = _dt(10, 0)
        ends = _dt(11, 0)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Weekly Standup",
            "starts_at": _iso(starts),
            "ends_at": _iso(ends),
            "series": {"freq": "weekly", "interval": 1, "count": 4},
        })
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert len(data["bookings"]) == 4
        assert data["series_id"] is not None

    async def test_series_bookings_share_series_id_and_rrule(self, requester, admin):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        starts = _dt(10, 0)
        ends = _dt(11, 0)
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Series Share Test",
            "starts_at": _iso(starts),
            "ends_at": _iso(ends),
            "series": {"freq": "weekly", "count": 3},
        })
        assert resp.status_code == 201, resp.text
        data = resp.json()
        series_id = data["series_id"]
        assert series_id is not None
        # All bookings share the same series_id and rrule
        for b in data["bookings"]:
            assert b["series_id"] == series_id
            assert b["rrule"] is not None
            assert "FREQ=WEEKLY" in b["rrule"]

    async def test_series_occurrence_3_conflict_returns_400_with_date(
        self, requester, admin, db_session
    ):
        """Series with conflict on occurrence 3 → 400 with occurrence_conflicts listing that date."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        # first occurrence is next week + 1 day
        occ1 = _dt(10, 0, days_ahead=1)
        occ1_end = _dt(11, 0, days_ahead=1)
        occ3_start = occ1 + timedelta(weeks=2)
        occ3_end = occ1_end + timedelta(weeks=2)

        # Block occurrence 3
        await _insert_booking_raw(db_session, room["id"], occ3_start, occ3_end)

        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Series With Conflict",
            "starts_at": _iso(occ1),
            "ends_at": _iso(occ1_end),
            "series": {"freq": "weekly", "count": 4},
        })
        assert resp.status_code == 400, resp.text
        data = resp.json()
        assert data["detail"] == "conflict"
        assert "occurrence_conflicts" in data
        assert len(data["occurrence_conflicts"]) >= 1
        # occurrence_conflicts entries must name the conflicting date
        occ3_date_str = occ3_start.astimezone(TZ).date().isoformat()
        occ_dates = [oc["date"] for oc in data["occurrence_conflicts"]]
        assert occ3_date_str in occ_dates, (
            f"Expected date {occ3_date_str} in occurrence_conflicts, got {occ_dates}"
        )

    async def test_series_conflict_zero_rows_persisted(self, requester, admin, db_session):
        """When series has a conflict, ZERO booking rows must be persisted."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        occ1 = _dt(10, 0, days_ahead=1)
        occ1_end = _dt(11, 0, days_ahead=1)
        occ2_start = occ1 + timedelta(weeks=1)
        occ2_end = occ1_end + timedelta(weeks=1)

        await _insert_booking_raw(db_session, room["id"], occ2_start, occ2_end)

        from sqlalchemy import select, func
        from app.models.booking import Booking as BookingModel
        count_before_result = await db_session.execute(
            select(func.count()).select_from(BookingModel).where(
                BookingModel.room_id == uuid.UUID(room["id"]),
                BookingModel.organizer_id == user.id,
            )
        )
        count_before = count_before_result.scalar()

        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Series Conflict",
            "starts_at": _iso(occ1),
            "ends_at": _iso(occ1_end),
            "series": {"freq": "weekly", "count": 3},
        })
        assert resp.status_code == 400, resp.text

        count_after_result = await db_session.execute(
            select(func.count()).select_from(BookingModel).where(
                BookingModel.room_id == uuid.UUID(room["id"]),
                BookingModel.organizer_id == user.id,
            )
        )
        count_after = count_after_result.scalar()
        assert count_before == count_after, "Zero rows must be persisted when series has a conflict"


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: GET /bookings/mine
# ─────────────────────────────────────────────────────────────────────────────

class TestGetBookingsMine:
    async def test_get_mine_returns_empty_list_initially(self, requester):
        user, req_client = requester
        resp = await req_client.get("/api/v1/bookings/mine")
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json(), list)

    async def test_get_mine_returns_own_bookings(self, requester, admin):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        # Create a booking
        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "My Meeting",
            "starts_at": _iso(_dt(10, 0)),
            "ends_at": _iso(_dt(11, 0)),
        })
        assert resp.status_code == 201, resp.text

        mine_resp = await req_client.get("/api/v1/bookings/mine")
        assert mine_resp.status_code == 200, mine_resp.text
        bookings = mine_resp.json()
        assert len(bookings) >= 1
        titles = [b["title"] for b in bookings]
        assert "My Meeting" in titles

    async def test_get_mine_does_not_return_others_bookings(
        self, requester, admin, test_engine, db_session
    ):
        """GET /bookings/mine must only return the calling user's bookings."""
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        # Other user inserts a booking via raw db_session
        other_user = await make_user(test_engine, role="requester")
        from app.models.booking import Booking as BookingModel
        other_booking = BookingModel(
            room_id=uuid.UUID(room["id"]),
            title="Other User Booking",
            organizer_id=other_user.id,
            attendee_ids=[],
            starts_at=_dt(14, 0),
            ends_at=_dt(15, 0),
            status="confirmed",
            calendar_uid=f"other-{uuid.uuid4()}@test",
        )
        db_session.add(other_booking)
        await db_session.flush()

        mine_resp = await req_client.get("/api/v1/bookings/mine")
        assert mine_resp.status_code == 200, mine_resp.text
        bookings = mine_resp.json()
        ids = [b["id"] for b in bookings]
        assert str(other_booking.id) not in ids

    async def test_get_mine_bookingout_has_room_fields(self, requester, admin):
        user, req_client = requester
        _, adm_client = admin
        room = await _create_room(adm_client)

        await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Room Field Test",
            "starts_at": _iso(_dt(10, 0)),
            "ends_at": _iso(_dt(11, 0)),
        })

        mine_resp = await req_client.get("/api/v1/bookings/mine")
        assert mine_resp.status_code == 200, mine_resp.text
        bookings = mine_resp.json()
        matching = [b for b in bookings if b["title"] == "Room Field Test"]
        assert len(matching) >= 1
        b = matching[0]
        assert "room_name" in b
        assert "room_code" in b
        assert "room_id" in b

    async def test_get_mine_requires_auth(self, client):
        resp = await client.get("/api/v1/bookings/mine")
        assert resp.status_code in (401, 403), resp.text
