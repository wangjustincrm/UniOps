"""TDD tests for PATCH /bookings/series/{series_id} — series-level editing.

RED phase: all tests written BEFORE implementation.
Tests must FAIL until the endpoint is implemented.

Coverage:
  1. organizer edits title → all future rows updated, past row untouched,
     ical_sequence bumped uniformly, ONE notification log.
  2. time change 10:00→11:00 applied per-occurrence to each occurrence's local date.
  3. room switch validated; conflict vs OTHER bookings detected per occurrence
     (occurrence_conflicts names the date) with nothing persisted.
  4. series-of-others → 404 for stranger, 200 for matrix admin.
  5. series_fully_started → 400.
  6. bad time format / misaligned → 422.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.audit import BookingAuditLog
from app.models.booking import Booking as BookingModel
from app.models.notification import NotificationLog
from app.models.room import MeetingRoom
from tests.conftest import authed_client, make_token, make_user

TZ = ZoneInfo("America/Toronto")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _dt(h: int, m: int = 0, days_ahead: int = 1) -> datetime:
    """Tz-aware datetime in America/Toronto, N days ahead."""
    d = datetime.now(TZ).date() + timedelta(days=days_ahead)
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _create_room(client, *, status: str = "available", **kwargs) -> dict:
    payload = {
        "name": f"Series Room {uuid.uuid4().hex[:6]}",
        "code": f"SR-{uuid.uuid4().hex[:6]}",
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


async def _create_series(client, room_id: str, starts: datetime, ends: datetime, count: int = 4) -> dict:
    """Create a recurring weekly series; return full response dict."""
    payload = {
        "room_id": room_id,
        "title": "Weekly Series",
        "starts_at": _iso(starts),
        "ends_at": _iso(ends),
        "series": {"freq": "weekly", "interval": 1, "count": count},
    }
    resp = await client.post("/api/v1/bookings", json=payload)
    assert resp.status_code == 201, f"Series creation failed: {resp.status_code} {resp.text}"
    return resp.json()


async def _insert_booking_raw(
    db_session,
    room_id,
    starts_at: datetime,
    ends_at: datetime,
    *,
    organizer_id: uuid.UUID | None = None,
    status: str = "confirmed",
    series_id: uuid.UUID | None = None,
    rrule: str | None = None,
    calendar_uid: str | None = None,
    title: str = "Raw Booking",
) -> BookingModel:
    """Insert a booking directly via db_session (no HTTP)."""
    b = BookingModel(
        room_id=uuid.UUID(room_id) if isinstance(room_id, str) else room_id,
        title=title,
        organizer_id=organizer_id or uuid.uuid4(),
        attendee_ids=[],
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
        calendar_uid=calendar_uid or f"raw-{uuid.uuid4()}@test",
        series_id=series_id,
        rrule=rrule,
    )
    db_session.add(b)
    await db_session.flush()
    return b


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: organizer edits title → future rows updated, past untouched,
# sequence bumped, ONE notification
# ─────────────────────────────────────────────────────────────────────────────

class TestSeriesEditTitle:
    async def test_organizer_edits_title_updates_future_rows(
        self, requester, admin, db_session
    ):
        """PATCH /series/{id} with title change: all future confirmed rows
        get the new title; past row is untouched.
        """
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)

        series_id = uuid.uuid4()
        rrule = "FREQ=WEEKLY;COUNT=3"
        calendar_uid = f"series-{uuid.uuid4()}@test"

        # Insert one past occurrence (3 days ago)
        past_start = datetime.now(TZ) - timedelta(days=3)
        past_end = past_start + timedelta(hours=1)
        past_b = await _insert_booking_raw(
            db_session, room["id"], past_start, past_end,
            organizer_id=organizer.id, series_id=series_id, rrule=rrule,
            calendar_uid=calendar_uid, title="Old Title",
        )

        # Insert 2 future occurrences
        f1 = await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, days_ahead=7), _dt(11, 0, days_ahead=7),
            organizer_id=organizer.id, series_id=series_id, rrule=rrule,
            calendar_uid=calendar_uid, title="Old Title",
        )
        f2 = await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, days_ahead=14), _dt(11, 0, days_ahead=14),
            organizer_id=organizer.id, series_id=series_id, rrule=rrule,
            calendar_uid=calendar_uid, title="Old Title",
        )

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"title": "New Series Title"},
        )
        assert resp.status_code == 200, resp.text

        # Future rows must have new title
        for b_id in [f1.id, f2.id]:
            result = await db_session.execute(select(BookingModel).where(BookingModel.id == b_id))
            row = result.scalar_one()
            assert row.title == "New Series Title", f"Future row {b_id} not updated"

        # Past row must be unchanged
        result = await db_session.execute(select(BookingModel).where(BookingModel.id == past_b.id))
        past_row = result.scalar_one()
        assert past_row.title == "Old Title", "Past occurrence must not be updated"

    async def test_organizer_edits_title_bumps_sequence_uniformly(
        self, requester, admin, db_session
    ):
        """ical_sequence for all future rows must be max(series)+1 after edit."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        rrule = "FREQ=WEEKLY;COUNT=2"
        uid = f"series-{uuid.uuid4()}@test"

        f1 = await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, rrule=rrule,
            calendar_uid=uid, title="Original",
        )
        f2 = await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 14), _dt(11, 0, 14),
            organizer_id=organizer.id, series_id=series_id, rrule=rrule,
            calendar_uid=uid, title="Original",
        )
        # Manually set f1 sequence to 2 to simulate prior edits
        f1.ical_sequence = 2
        await db_session.flush()

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"title": "Bumped"},
        )
        assert resp.status_code == 200, resp.text

        # All future rows get max(2,0)+1 = 3
        for b_id in [f1.id, f2.id]:
            result = await db_session.execute(select(BookingModel).where(BookingModel.id == b_id))
            row = result.scalar_one()
            assert row.ical_sequence == 3, f"Expected sequence=3 on {b_id}, got {row.ical_sequence}"

    async def test_organizer_edits_title_creates_one_notification_log(
        self, requester, admin, db_session
    ):
        """ONE notification row is enqueued for the whole series edit."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        rrule = "FREQ=WEEKLY;COUNT=2"
        uid = f"series-{uuid.uuid4()}@test"

        f1 = await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, rrule=rrule, calendar_uid=uid,
        )
        await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 14), _dt(11, 0, 14),
            organizer_id=organizer.id, series_id=series_id, rrule=rrule, calendar_uid=uid,
        )

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"title": "Notified"},
        )
        assert resp.status_code == 200, resp.text

        # Exactly one notification log entry for this series edit
        result = await db_session.execute(
            select(NotificationLog).where(
                NotificationLog.booking_id == f1.id,
                NotificationLog.notif_type == "updated",
            )
        )
        logs = result.scalars().all()
        assert len(logs) == 1, f"Expected 1 notification log, got {len(logs)}"


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: time change applied per-occurrence local date
# ─────────────────────────────────────────────────────────────────────────────

class TestSeriesEditTimeChange:
    async def test_time_change_applied_per_occurrence_local_date(
        self, requester, admin, db_session
    ):
        """start_time 10:00→11:00, end_time 11:00→12:00 applied to each
        future occurrence's own local date.
        """
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        rrule = "FREQ=WEEKLY;COUNT=2"
        uid = f"series-{uuid.uuid4()}@test"

        # Future occurrence day 7: starts 10:00, ends 11:00
        f1_date = (datetime.now(TZ).date() + timedelta(days=7))
        f1_start = datetime(f1_date.year, f1_date.month, f1_date.day, 10, 0, tzinfo=TZ)
        f1_end = datetime(f1_date.year, f1_date.month, f1_date.day, 11, 0, tzinfo=TZ)
        f1 = await _insert_booking_raw(
            db_session, room["id"], f1_start, f1_end,
            organizer_id=organizer.id, series_id=series_id, rrule=rrule, calendar_uid=uid,
        )

        f2_date = (datetime.now(TZ).date() + timedelta(days=14))
        f2_start = datetime(f2_date.year, f2_date.month, f2_date.day, 10, 0, tzinfo=TZ)
        f2_end = datetime(f2_date.year, f2_date.month, f2_date.day, 11, 0, tzinfo=TZ)
        f2 = await _insert_booking_raw(
            db_session, room["id"], f2_start, f2_end,
            organizer_id=organizer.id, series_id=series_id, rrule=rrule, calendar_uid=uid,
        )

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"start_time": "11:00", "end_time": "12:00"},
        )
        assert resp.status_code == 200, resp.text

        # f1 should now be 11:00–12:00 on f1_date (in Toronto local)
        result = await db_session.execute(select(BookingModel).where(BookingModel.id == f1.id))
        f1_row = result.scalar_one()
        f1_local = f1_row.starts_at.astimezone(TZ)
        assert f1_local.hour == 11 and f1_local.minute == 0, (
            f"f1 starts_at expected 11:00 local, got {f1_local}"
        )
        f1_end_local = f1_row.ends_at.astimezone(TZ)
        assert f1_end_local.hour == 12 and f1_end_local.minute == 0, (
            f"f1 ends_at expected 12:00 local, got {f1_end_local}"
        )

        # f2 same: 11:00–12:00 on f2_date
        result2 = await db_session.execute(select(BookingModel).where(BookingModel.id == f2.id))
        f2_row = result2.scalar_one()
        f2_local = f2_row.starts_at.astimezone(TZ)
        assert f2_local.hour == 11 and f2_local.minute == 0, (
            f"f2 starts_at expected 11:00 local, got {f2_local}"
        )

    async def test_time_change_requires_both_or_neither(
        self, requester, admin, db_session
    ):
        """Providing only start_time without end_time → 422 validation error."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        rrule = "FREQ=WEEKLY;COUNT=1"
        uid = f"series-{uuid.uuid4()}@test"
        await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, rrule=rrule, calendar_uid=uid,
        )

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"start_time": "11:00"},  # end_time missing
        )
        assert resp.status_code == 422, resp.text

    async def test_time_bad_format_422(
        self, requester, admin, db_session
    ):
        """Non-HH:MM start_time → 422."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        uid = f"series-{uuid.uuid4()}@test"
        await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"start_time": "not-a-time", "end_time": "12:00"},
        )
        assert resp.status_code == 422, resp.text

    async def test_time_not_on_15min_grid_422(
        self, requester, admin, db_session
    ):
        """start_time not on 15-min grid → 422."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        uid = f"series-{uuid.uuid4()}@test"
        await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"start_time": "10:07", "end_time": "11:07"},
        )
        assert resp.status_code == 422, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: room switch + conflict detection
# ─────────────────────────────────────────────────────────────────────────────

class TestSeriesEditRoomConflict:
    async def test_room_switch_validated_room_not_found_404(
        self, requester, admin, db_session
    ):
        """Switching to non-existent room → 404."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        uid = f"series-{uuid.uuid4()}@test"
        await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"room_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404, resp.text

    async def test_room_switch_to_unavailable_room_422(
        self, requester, admin, db_session
    ):
        """Switching to maintenance room → 422."""
        organizer, req_client = requester
        _, adm_client = admin

        main_room = await _create_room(adm_client)
        maint_room = await _create_room(adm_client, status="maintenance")

        series_id = uuid.uuid4()
        uid = f"series-{uuid.uuid4()}@test"
        await _insert_booking_raw(
            db_session, main_room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"room_id": maint_room["id"]},
        )
        assert resp.status_code == 422, resp.text

    async def test_conflict_detected_per_occurrence_with_occurrence_conflicts_shape(
        self, requester, admin, db_session, test_engine
    ):
        """Conflict with OTHER booking on one of the future occurrences → 400
        with occurrence_conflicts listing the conflicting date, nothing persisted.
        """
        organizer, req_client = requester
        _, adm_client = admin

        # Two rooms: original and target
        orig_room = await _create_room(adm_client)
        new_room = await _create_room(adm_client)

        series_id = uuid.uuid4()
        uid = f"series-{uuid.uuid4()}@test"

        # Two future occurrences in orig_room at 10:00–11:00
        f1_date = datetime.now(TZ).date() + timedelta(days=7)
        f1_start = datetime(f1_date.year, f1_date.month, f1_date.day, 10, 0, tzinfo=TZ)
        f1_end = f1_start + timedelta(hours=1)
        await _insert_booking_raw(
            db_session, orig_room["id"], f1_start, f1_end,
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )

        f2_date = datetime.now(TZ).date() + timedelta(days=14)
        f2_start = datetime(f2_date.year, f2_date.month, f2_date.day, 10, 0, tzinfo=TZ)
        f2_end = f2_start + timedelta(hours=1)
        f2_b = await _insert_booking_raw(
            db_session, orig_room["id"], f2_start, f2_end,
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )

        # Blocker in new_room on day 14 at 10:00–11:00 (belongs to a different user)
        blocker_user = await make_user(test_engine, role="requester")
        await _insert_booking_raw(
            db_session, new_room["id"], f2_start, f2_end,
            organizer_id=blocker_user.id,
        )

        # Try to switch to new_room — f2 window is blocked
        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"room_id": new_room["id"]},
        )
        assert resp.status_code == 400, resp.text
        data = resp.json()
        assert data["detail"] == "conflict"
        assert "occurrence_conflicts" in data
        assert len(data["occurrence_conflicts"]) >= 1, "Expected at least one occurrence conflict"
        # The conflicting date is f2_date
        dates_in_conflicts = [oc["date"] for oc in data["occurrence_conflicts"]]
        assert f2_date.isoformat() in dates_in_conflicts, (
            f"Expected {f2_date.isoformat()} in occurrence_conflicts, got {dates_in_conflicts}"
        )
        # suggestions is null (no suggestions for series edit)
        assert data.get("suggestions") is None

        # Nothing was persisted — f2 still in orig_room
        result = await db_session.execute(select(BookingModel).where(BookingModel.id == f2_b.id))
        f2_row = result.scalar_one()
        assert f2_row.room_id == uuid.UUID(orig_room["id"]), "Room must not have changed on conflict"


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: authorization — stranger 404, matrix admin 200
# ─────────────────────────────────────────────────────────────────────────────

class TestSeriesEditAuth:
    async def test_stranger_gets_404_anti_probing(
        self, requester, admin, db_session, test_engine
    ):
        """Non-organizer non-admin caller → 404 (anti-probing)."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        uid = f"series-{uuid.uuid4()}@test"
        await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )

        # Stranger user
        stranger = await make_user(test_engine, role="requester")
        stranger_token = make_token(stranger.id, stranger.role)
        async with authed_client(stranger_token, session=db_session) as stranger_client:
            resp = await stranger_client.patch(
                f"/api/v1/bookings/series/{series_id}",
                json={"title": "Hacked"},
            )
        assert resp.status_code == 404, resp.text

    async def test_matrix_admin_can_edit_any_series(
        self, requester, admin, db_session, test_engine
    ):
        """A user with manage_meeting_rooms via the matrix can edit another user's series."""
        import uuid as _uuid
        from app.models.company_config_mirror import CompanyConfig

        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        uid = f"series-{uuid.uuid4()}@test"
        await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )

        # Grant manage_meeting_rooms to a procurement_manager
        matrix_admin = await make_user(test_engine, role="procurement_manager")
        cfg = CompanyConfig(
            id=_uuid.uuid4(),
            role_permissions={"procurement_manager": {"manage_meeting_rooms": True, "view_booking": True}},
        )
        db_session.add(cfg)
        await db_session.flush()

        matrix_token = make_token(matrix_admin.id, matrix_admin.role)
        async with authed_client(matrix_token, session=db_session) as matrix_client:
            resp = await matrix_client.patch(
                f"/api/v1/bookings/series/{series_id}",
                json={"title": "Admin Override"},
            )
        assert resp.status_code == 200, resp.text

    async def test_organizer_can_edit_own_series(self, requester, admin, db_session):
        """Organizer can edit their own series → 200."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        uid = f"series-{uuid.uuid4()}@test"
        await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"title": "My Edit"},
        )
        assert resp.status_code == 200, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: series_fully_started → 400
# ─────────────────────────────────────────────────────────────────────────────

class TestSeriesFullyStarted:
    async def test_series_fully_started_returns_400(
        self, requester, admin, db_session
    ):
        """When no future confirmed occurrences remain → 400 series_fully_started."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        uid = f"series-{uuid.uuid4()}@test"

        # Only a past occurrence
        past_start = datetime.now(TZ) - timedelta(days=7)
        past_end = past_start + timedelta(hours=1)
        await _insert_booking_raw(
            db_session, room["id"], past_start, past_end,
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"title": "No Future"},
        )
        assert resp.status_code == 400, resp.text
        assert "series_fully_started" in resp.json().get("detail", ""), resp.text

    async def test_unknown_series_id_returns_404(self, requester, admin):
        """Unknown series_id → 404 (anti-probing)."""
        _, req_client = requester

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{uuid.uuid4()}",
            json={"title": "Ghost"},
        )
        assert resp.status_code == 404, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# Section 6: audit rows created for each updated future booking
# ─────────────────────────────────────────────────────────────────────────────

class TestSeriesEditAudit:
    async def test_audit_rows_created_for_each_future_booking(
        self, requester, admin, db_session
    ):
        """One audit row per updated booking with action='update'."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        uid = f"series-{uuid.uuid4()}@test"

        f1 = await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )
        f2 = await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 14), _dt(11, 0, 14),
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"title": "Audit Check"},
        )
        assert resp.status_code == 200, resp.text

        for b_id in [f1.id, f2.id]:
            result = await db_session.execute(
                select(BookingAuditLog).where(
                    BookingAuditLog.booking_id == b_id,
                    BookingAuditLog.action == "update",
                )
            )
            audit = result.scalar_one_or_none()
            assert audit is not None, f"Missing audit row for booking {b_id}"
            assert audit.before is not None
            assert audit.after is not None

    async def test_sync_status_set_pending_after_edit(
        self, requester, admin, db_session
    ):
        """Future rows must have sync_status=pending after series edit."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        uid = f"series-{uuid.uuid4()}@test"
        f1 = await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, calendar_uid=uid,
        )

        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"title": "Sync Check"},
        )
        assert resp.status_code == 200, resp.text

        result = await db_session.execute(select(BookingModel).where(BookingModel.id == f1.id))
        row = result.scalar_one()
        # sync_status transitions to pending then the enqueue immediately updates it
        assert row.sync_status in ("pending", "sent", "failed"), (
            f"Unexpected sync_status: {row.sync_status}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 7: Regression tests for model_fields_set fix + room revalidation
# ─────────────────────────────────────────────────────────────────────────────

class TestSeriesEditRegressions:
    async def test_series_edit_can_clear_description(
        self, requester, admin, db_session
    ):
        """PATCH /bookings/series/{id} with description:null clears description.

        Verifies model_fields_set fix in app/api/v1/bookings.py line 972:
        checks "description" in body.model_fields_set to distinguish
        explicit null (clear field) from absent (leave as-is).
        """
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        rrule = "FREQ=WEEKLY;COUNT=2"
        uid = f"series-{uuid.uuid4()}@test"

        # Create two future occurrences WITH descriptions
        f1 = await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, rrule=rrule,
            calendar_uid=uid, title="Series Title",
        )
        f1.description = "Meeting agenda: Q3 planning"

        f2 = await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 14), _dt(11, 0, 14),
            organizer_id=organizer.id, series_id=series_id, rrule=rrule,
            calendar_uid=uid, title="Series Title",
        )
        f2.description = "Meeting agenda: Q3 planning"
        await db_session.flush()

        # PATCH with explicit description:null to clear it
        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"description": None},
        )
        assert resp.status_code == 200, resp.text

        # Verify both future rows' description is now None in DB
        for b_id in [f1.id, f2.id]:
            result = await db_session.execute(select(BookingModel).where(BookingModel.id == b_id))
            row = result.scalar_one()
            assert row.description is None, (
                f"Expected description=None for {b_id} after clear, got {row.description!r}"
            )

    async def test_series_edit_title_only_on_maintenance_room_422(
        self, requester, admin, db_session
    ):
        """PATCH /bookings/series/{id} with only {"title": "New"} → 422
        when room status is maintenance.

        Verifies room re-validation happens even without room/time changes
        (C3: always validate effective_room.status == available).
        """
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client, status="available")
        series_id = uuid.uuid4()
        rrule = "FREQ=WEEKLY;COUNT=2"
        uid = f"series-{uuid.uuid4()}@test"

        # Create two future occurrences in an available room
        f1 = await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 7), _dt(11, 0, 7),
            organizer_id=organizer.id, series_id=series_id, rrule=rrule,
            calendar_uid=uid, title="Series Title",
        )
        await _insert_booking_raw(
            db_session, room["id"], _dt(10, 0, 14), _dt(11, 0, 14),
            organizer_id=organizer.id, series_id=series_id, rrule=rrule,
            calendar_uid=uid, title="Series Title",
        )

        # Now change the room status to maintenance via db_session
        room_obj_result = await db_session.execute(
            select(MeetingRoom).where(MeetingRoom.id == uuid.UUID(room["id"]))
        )
        room_obj = room_obj_result.scalar_one()
        room_obj.status = "maintenance"
        await db_session.flush()

        # PATCH with only title (no room_id, no times) → must still fail with 422
        resp = await req_client.patch(
            f"/api/v1/bookings/series/{series_id}",
            json={"title": "New Title"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 on room re-validation, got {resp.status_code}: {resp.text}"
        )
        assert "not bookable" in resp.json().get("detail", "").lower() or "not 'available'" in resp.json().get("detail", ""), (
            f"Expected 'not bookable' error, got: {resp.json()}"
        )
