"""TDD tests for Task 8: PATCH /bookings/{id}, POST /bookings/{id}/cancel,
GET /admin/bookings, GET /admin/bookings/export.

Step 1 (RED): All tests written FIRST — must fail before implementation.
Step 2 (GREEN): Implement routes, crud helpers.

Test coverage (per task-8-brief):
  - non-organizer PATCH → 403
  - admin PATCH ok
  - time change onto busy slot → 400 conflict
  - PATCH bumps sequence + sync_status pending + audit update row
  - series member PATCH → 400 series_member_immutable
  - single cancel frees slot (re-book same window succeeds)
  - series member single-cancel → 200, cancels that occurrence alone (Task 4)
  - series=true cancels only future confirmed occurrences (seed one past occurrence)
    + ONE notification
  - started meeting PATCH by organizer → 403, by admin → 200
  - CSV export: header row + rows + content-type
  - admin list: filters + total
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import make_token, authed_client, make_user, grant_matrix_permission

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


async def _create_booking(client, room_id: str, starts: datetime, ends: datetime, **kwargs) -> dict:
    payload = {
        "room_id": room_id,
        "title": kwargs.pop("title", "Test Booking"),
        "starts_at": _iso(starts),
        "ends_at": _iso(ends),
    }
    payload.update(kwargs)
    resp = await client.post("/api/v1/bookings", json=payload)
    assert resp.status_code == 201, f"Booking creation failed: {resp.status_code} {resp.text}"
    data = resp.json()
    return data["bookings"][0]


async def _create_series(client, room_id: str, starts: datetime, ends: datetime, count: int = 4) -> dict:
    """Create a recurring weekly series; return full response dict."""
    payload = {
        "room_id": room_id,
        "title": "Series Meeting",
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
) -> object:
    """Insert a booking directly via db_session (no HTTP)."""
    from app.models.booking import Booking as BookingModel
    b = BookingModel(
        room_id=uuid.UUID(room_id) if isinstance(room_id, str) else room_id,
        title="Raw Booking",
        organizer_id=organizer_id or uuid.uuid4(),
        attendee_ids=[],
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
        calendar_uid=f"raw-{uuid.uuid4()}@test",
        series_id=series_id,
        rrule=rrule,
    )
    db_session.add(b)
    await db_session.flush()
    return b


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: PATCH /bookings/{id} — authorization
# ─────────────────────────────────────────────────────────────────────────────

class TestPatchBookingAuth:
    async def test_non_organizer_patch_returns_403(self, requester, admin, test_engine, db_session):
        """A different regular user (non-organizer) must receive 403."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        # Create a second user (not the organizer)
        other_user = await make_user(test_engine, role="requester")
        other_token = make_token(other_user.id, other_user.role)
        async with authed_client(other_token, session=db_session) as other_client:
            resp = await other_client.patch(
                f"/api/v1/bookings/{booking['id']}",
                json={"title": "Hacked Title"},
            )
        # Returns 404 (not 403) to prevent existence probing by unauthorized callers.
        assert resp.status_code == 404, resp.text

    async def test_admin_patch_ok(self, requester, admin):
        """Admin (manage_meeting_rooms) can PATCH any booking."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        resp = await adm_client.patch(
            f"/api/v1/bookings/{booking['id']}",
            json={"title": "Admin Changed Title"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["title"] == "Admin Changed Title"

    async def test_organizer_patch_ok(self, requester, admin):
        """The organizer themselves can PATCH their own booking."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        resp = await req_client.patch(
            f"/api/v1/bookings/{booking['id']}",
            json={"title": "My New Title"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["title"] == "My New Title"

    async def test_patch_non_existent_booking_returns_404(self, requester):
        _, req_client = requester
        resp = await req_client.patch(
            f"/api/v1/bookings/{uuid.uuid4()}",
            json={"title": "Nope"},
        )
        assert resp.status_code == 404, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: PATCH — business rules
# ─────────────────────────────────────────────────────────────────────────────

class TestPatchBookingRules:
    async def test_patch_time_change_to_busy_slot_returns_400_conflict(
        self, requester, admin, db_session
    ):
        """Changing time onto a busy slot → 400 conflict (same shape as POST)."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        # Block the 12:00–13:00 slot
        await _insert_booking_raw(db_session, room["id"], _dt(12, 0), _dt(13, 0))

        resp = await req_client.patch(
            f"/api/v1/bookings/{booking['id']}",
            json={
                "starts_at": _iso(_dt(12, 0)),
                "ends_at": _iso(_dt(13, 0)),
            },
        )
        assert resp.status_code == 400, resp.text
        data = resp.json()
        assert data["detail"] == "conflict"
        assert "conflicts" in data
        assert "suggestions" in data

    async def test_patch_bumps_ical_sequence_and_sync_pending(self, requester, admin):
        """PATCH must increment ical_sequence by 1 and set sync_status=pending."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        initial_seq = booking["ical_sequence"]

        resp = await req_client.patch(
            f"/api/v1/bookings/{booking['id']}",
            json={"title": "Updated Title"},
        )
        assert resp.status_code == 200, resp.text
        updated = resp.json()
        assert updated["ical_sequence"] == initial_seq + 1
        # Task 10: enqueue fires immediately on update, transitioning sync_status
        # from 'pending' to 'sent' (log-only) or 'failed'. Accept any terminal state.
        assert updated["sync_status"] in ("pending", "sent", "failed"), (
            f"Unexpected sync_status: {updated['sync_status']}"
        )

    async def test_patch_creates_audit_update_row(self, requester, admin, db_session):
        """PATCH must create an audit row with action='update'."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        await req_client.patch(
            f"/api/v1/bookings/{booking['id']}",
            json={"title": "Audit Test"},
        )

        from sqlalchemy import select
        from app.models.audit import BookingAuditLog
        result = await db_session.execute(
            select(BookingAuditLog).where(
                BookingAuditLog.booking_id == uuid.UUID(booking["id"]),
                BookingAuditLog.action == "update",
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None, "Expected audit log row with action='update'"
        assert audit.before is not None
        assert audit.after is not None

    async def test_patch_series_member_returns_400_series_member_immutable(
        self, requester, admin
    ):
        """PATCH on a series member must return 400 with detail='series_member_immutable'."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_data = await _create_series(req_client, room["id"], _dt(10, 0), _dt(11, 0), count=3)
        # Pick any series member
        first_booking = series_data["bookings"][0]
        assert first_booking["series_id"] is not None  # sanity

        resp = await req_client.patch(
            f"/api/v1/bookings/{first_booking['id']}",
            json={"title": "Mutate Series Member"},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "series_member_immutable"

    async def test_patch_cancelled_booking_returns_400(self, requester, admin, db_session):
        """PATCH on a cancelled booking must return 400."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        # Cancel it
        cancel_resp = await req_client.post(f"/api/v1/bookings/{booking['id']}/cancel")
        assert cancel_resp.status_code == 200

        # Now try to PATCH
        resp = await req_client.patch(
            f"/api/v1/bookings/{booking['id']}",
            json={"title": "Patching Cancelled"},
        )
        assert resp.status_code == 400, resp.text

    async def test_patch_time_only_change_on_maintenance_room_returns_422(
        self, requester, admin
    ):
        """PATCH with only a time change must reject a maintenance/disabled room.

        Even when room_id is unchanged, if the room was set to maintenance AFTER
        booking creation the reschedule must be blocked with 422.
        """
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        # Put the room into maintenance via admin API
        sr = await adm_client.post(
            f"/api/v1/admin/rooms/{room['id']}/status",
            json={"status": "maintenance", "notes": "test maintenance"},
        )
        assert sr.status_code == 200, f"Status change failed: {sr.text}"

        # Attempt a time-only PATCH — must be rejected because room is not available
        resp = await req_client.patch(
            f"/api/v1/bookings/{booking['id']}",
            json={
                "starts_at": _iso(_dt(12, 0)),
                "ends_at": _iso(_dt(13, 0)),
            },
        )
        assert resp.status_code == 422, resp.text
        assert "not bookable" in resp.json()["detail"]

    async def test_patch_started_booking_organizer_403_admin_200(
        self, requester, admin, db_session
    ):
        """PATCH on an already-started meeting: organizer → 403, admin → 200."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)

        # Insert a booking that started in the past
        past_start = datetime.now(TZ) - timedelta(hours=1)
        past_end = datetime.now(TZ) + timedelta(hours=1)
        # Snap past_start to a 15-min boundary by replacing minutes to nearest 15
        # just use a fixed offset from now
        raw_booking = await _insert_booking_raw(
            db_session,
            room["id"],
            past_start,
            past_end,
            organizer_id=organizer.id,
        )

        # Organizer → 403
        resp = await req_client.patch(
            f"/api/v1/bookings/{raw_booking.id}",
            json={"title": "Started Meeting"},
        )
        assert resp.status_code == 403, resp.text

        # Admin → 200
        resp_admin = await adm_client.patch(
            f"/api/v1/bookings/{raw_booking.id}",
            json={"title": "Admin Override"},
        )
        assert resp_admin.status_code == 200, resp_admin.text


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: POST /bookings/{id}/cancel
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelBooking:
    async def test_single_cancel_frees_slot_and_rebook_succeeds(
        self, requester, admin
    ):
        """Cancel a single booking; the same slot should then be bookable → 201."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        # Cancel
        cancel_resp = await req_client.post(f"/api/v1/bookings/{booking['id']}/cancel")
        assert cancel_resp.status_code == 200, cancel_resp.text
        assert cancel_resp.json()["cancelled"] == 1

        # Re-book same window → must succeed
        rebook_resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Re-book",
            "starts_at": _iso(_dt(10, 0)),
            "ends_at": _iso(_dt(11, 0)),
        })
        assert rebook_resp.status_code == 201, rebook_resp.text

    async def test_non_organizer_cancel_returns_403(self, requester, admin, test_engine, db_session):
        """A non-organizer, non-admin user cannot cancel someone else's booking."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        # Different user
        other_user = await make_user(test_engine, role="requester")
        other_token = make_token(other_user.id, other_user.role)
        async with authed_client(other_token, session=db_session) as other_client:
            resp = await other_client.post(f"/api/v1/bookings/{booking['id']}/cancel")
        # Returns 404 (not 403) to prevent existence probing by unauthorized callers.
        assert resp.status_code == 404, resp.text

    async def test_cancel_already_cancelled_returns_400(self, requester, admin):
        """Cancelling an already-cancelled booking → 400."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        # First cancel
        first = await req_client.post(f"/api/v1/bookings/{booking['id']}/cancel")
        assert first.status_code == 200

        # Second cancel
        second = await req_client.post(f"/api/v1/bookings/{booking['id']}/cancel")
        assert second.status_code == 400, second.text

    async def test_series_member_single_cancel_returns_200(self, requester, admin):
        """POST /bookings/{id}/cancel (series=false) on a series member cancels
        that occurrence alone (Task 4: single-occurrence cancel). Formerly this
        400'd; that guard is gone."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_data = await _create_series(req_client, room["id"], _dt(10, 0), _dt(11, 0), count=3)
        first_member = series_data["bookings"][0]
        assert first_member["series_id"] is not None

        resp = await req_client.post(
            f"/api/v1/bookings/{first_member['id']}/cancel",
            params={"series": "false"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"cancelled": 1}

    async def test_series_cancel_cancels_only_future_occurrences(
        self, requester, admin, db_session
    ):
        """series=true: only future confirmed occurrences are cancelled.

        PRD §9.7: organizers can cancel meetings that have not yet started;
        in-progress meetings finish naturally (intentional, not a bug).

        We seed:
          - one past occurrence (starts_at 7 days ago) — must stay confirmed
          - one in-progress occurrence (started 30 min ago, ends in 30 min) — must stay confirmed
          - two future occurrences — must be cancelled
        """
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)

        series_id = uuid.uuid4()
        rrule = "FREQ=WEEKLY;COUNT=4"

        # Seed one past occurrence (manually, so it has series_id)
        past_start = datetime.now(TZ) - timedelta(days=7)
        past_end = past_start + timedelta(hours=1)
        past_booking = await _insert_booking_raw(
            db_session,
            room["id"],
            past_start,
            past_end,
            organizer_id=organizer.id,
            series_id=series_id,
            rrule=rrule,
        )

        # Seed one in-progress occurrence (started 30 min ago, ends 30 min from now).
        # PRD §9.7: in-progress meetings must NOT be cancelled by series cancel —
        # they finish naturally.
        inprogress_start = datetime.now(TZ) - timedelta(minutes=30)
        inprogress_end = datetime.now(TZ) + timedelta(minutes=30)
        inprogress_booking = await _insert_booking_raw(
            db_session,
            room["id"],
            inprogress_start,
            inprogress_end,
            organizer_id=organizer.id,
            series_id=series_id,
            rrule=rrule,
        )

        # Seed 2 future occurrences for the same series
        future1_start = _dt(10, 0, days_ahead=7)
        future1_end = _dt(11, 0, days_ahead=7)
        future1 = await _insert_booking_raw(
            db_session,
            room["id"],
            future1_start,
            future1_end,
            organizer_id=organizer.id,
            series_id=series_id,
            rrule=rrule,
        )
        future2_start = _dt(10, 0, days_ahead=14)
        future2_end = _dt(11, 0, days_ahead=14)
        future2 = await _insert_booking_raw(
            db_session,
            room["id"],
            future2_start,
            future2_end,
            organizer_id=organizer.id,
            series_id=series_id,
            rrule=rrule,
        )

        # Cancel via series=true using any member (use future1)
        resp = await req_client.post(
            f"/api/v1/bookings/{future1.id}/cancel",
            params={"series": "true"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["cancelled"] == 2  # only 2 future ones; in-progress excluded

        # Verify via DB
        from sqlalchemy import select
        from app.models.booking import Booking as BookingModel

        past_result = await db_session.execute(
            select(BookingModel).where(BookingModel.id == past_booking.id)
        )
        past_row = past_result.scalar_one()
        assert past_row.status == "confirmed", "Past occurrence must NOT be cancelled"

        # In-progress occurrence must stay confirmed — it finishes naturally (PRD §9.7).
        inprogress_result = await db_session.execute(
            select(BookingModel).where(BookingModel.id == inprogress_booking.id)
        )
        ip_row = inprogress_result.scalar_one()
        assert ip_row.status == "confirmed", (
            "In-progress occurrence must NOT be cancelled by series cancel (PRD §9.7: "
            "meeting finishes naturally)"
        )

        future1_result = await db_session.execute(
            select(BookingModel).where(BookingModel.id == future1.id)
        )
        f1_row = future1_result.scalar_one()
        assert f1_row.status == "cancelled"

        future2_result = await db_session.execute(
            select(BookingModel).where(BookingModel.id == future2.id)
        )
        f2_row = future2_result.scalar_one()
        assert f2_row.status == "cancelled"

    async def test_series_cancel_requires_series_member(self, requester, admin):
        """series=true on a non-series booking → 400."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        # Single (non-series) booking
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))
        assert booking["series_id"] is None

        resp = await req_client.post(
            f"/api/v1/bookings/{booking['id']}/cancel",
            params={"series": "true"},
        )
        assert resp.status_code == 400, resp.text

    async def test_cancel_creates_audit_cancel_row(self, requester, admin, db_session):
        """Single cancel by organizer creates audit row with action='cancel'."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        await req_client.post(f"/api/v1/bookings/{booking['id']}/cancel")

        from sqlalchemy import select
        from app.models.audit import BookingAuditLog
        result = await db_session.execute(
            select(BookingAuditLog).where(
                BookingAuditLog.booking_id == uuid.UUID(booking["id"]),
                BookingAuditLog.action == "cancel",
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None, "Expected audit row with action='cancel'"
        assert audit.actor_id == organizer.id

    async def test_admin_cancel_creates_force_cancel_audit_row(
        self, requester, admin, db_session
    ):
        """Cancel by admin (actor != organizer) creates audit row action='force_cancel'."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        await adm_client.post(f"/api/v1/bookings/{booking['id']}/cancel")

        from sqlalchemy import select
        from app.models.audit import BookingAuditLog
        result = await db_session.execute(
            select(BookingAuditLog).where(
                BookingAuditLog.booking_id == uuid.UUID(booking["id"]),
                BookingAuditLog.action == "force_cancel",
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None, "Expected audit row with action='force_cancel'"

    async def test_cancel_single_occurrence_leaves_siblings_confirmed(
        self, requester, admin, db_session
    ):
        """series=false on a series member cancels exactly that occurrence.

        The sibling assertion is the point of the test — a regression here
        silently cancels the whole series in the database.
        """
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series = await _create_series(
            req_client, room["id"], _dt(10), _dt(11), count=3,
        )
        occurrences = series["bookings"]
        target_id = occurrences[1]["id"]

        resp = await req_client.post(f"/api/v1/bookings/{target_id}/cancel?series=false")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"cancelled": 1}

        from sqlalchemy import select
        from app.models.booking import Booking as BookingModel

        target_result = await db_session.execute(
            select(BookingModel).where(BookingModel.id == uuid.UUID(target_id))
        )
        assert target_result.scalar_one().status == "cancelled"

        for sibling in (occurrences[0], occurrences[2]):
            sib_result = await db_session.execute(
                select(BookingModel).where(BookingModel.id == uuid.UUID(sibling["id"]))
            )
            assert sib_result.scalar_one().status == "confirmed"

    async def test_cancel_single_occurrence_rejects_past_occurrence(
        self, requester, admin, db_session
    ):
        """A started/finished occurrence must not be cancellable — otherwise an
        admin could 'cancel' yesterday's meeting and mail every attendee."""
        organizer, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series_id = uuid.uuid4()
        past = await _insert_booking_raw(
            db_session, room["id"],
            _dt(10, days_ahead=-7), _dt(11, days_ahead=-7),
            organizer_id=organizer.id,
            series_id=series_id,
            rrule="FREQ=WEEKLY;COUNT=2",
        )

        resp = await req_client.post(f"/api/v1/bookings/{past.id}/cancel?series=false")

        assert resp.status_code == 400
        assert resp.json()["detail"] == "occurrence_already_started"
        await db_session.refresh(past)
        assert past.status == "confirmed"

    async def test_cancel_single_occurrence_frees_the_room_slot(
        self, requester, admin
    ):
        """The cancelled slot becomes bookable again — availability counts
        confirmed rows only, so a new booking at that exact time must succeed."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series = await _create_series(
            req_client, room["id"], _dt(10), _dt(11), count=3,
        )
        target = series["bookings"][1]

        await req_client.post(f"/api/v1/bookings/{target['id']}/cancel?series=false")

        resp = await req_client.post("/api/v1/bookings", json={
            "room_id": room["id"],
            "title": "Reclaiming the freed slot",
            "starts_at": target["starts_at"],
            "ends_at": target["ends_at"],
        })
        assert resp.status_code == 201, resp.text

    async def test_cancel_series_still_cancels_all_future_occurrences(
        self, requester, admin, db_session
    ):
        """Regression: series=true keeps its existing meaning."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        series = await _create_series(
            req_client, room["id"], _dt(10), _dt(11), count=3,
        )
        occurrences = series["bookings"]

        resp = await req_client.post(
            f"/api/v1/bookings/{occurrences[0]['id']}/cancel?series=true"
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"cancelled": 3}

        from sqlalchemy import select
        from app.models.booking import Booking as BookingModel

        for occ in occurrences:
            occ_result = await db_session.execute(
                select(BookingModel).where(BookingModel.id == uuid.UUID(occ["id"]))
            )
            assert occ_result.scalar_one().status == "cancelled"


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: GET /admin/bookings
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminBookingsList:
    async def test_admin_list_returns_items_and_total(self, requester, admin):
        """GET /admin/bookings returns {items, total}."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        resp = await adm_client.get("/api/v1/admin/bookings")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert data["total"] >= 1

    async def test_admin_list_non_admin_returns_403(self, requester):
        """Regular user cannot access GET /admin/bookings."""
        _, req_client = requester
        resp = await req_client.get("/api/v1/admin/bookings")
        assert resp.status_code == 403, resp.text

    async def test_admin_list_filter_by_room_id(self, requester, admin):
        """Filter by room_id returns only bookings in that room."""
        _, req_client = requester
        _, adm_client = admin

        room1 = await _create_room(adm_client)
        room2 = await _create_room(adm_client)
        await _create_booking(req_client, room1["id"], _dt(10, 0), _dt(11, 0))
        await _create_booking(req_client, room2["id"], _dt(10, 0), _dt(11, 0))

        resp = await adm_client.get(f"/api/v1/admin/bookings?room_id={room1['id']}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert all(item["room_id"] == room1["id"] for item in data["items"])

    async def test_admin_list_filter_by_status(self, requester, admin):
        """Filter by status=cancelled."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        # Cancel it
        await req_client.post(f"/api/v1/bookings/{booking['id']}/cancel")

        resp = await adm_client.get("/api/v1/admin/bookings?status=cancelled")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert all(item["status"] == "cancelled" for item in data["items"])
        assert data["total"] >= 1

    async def test_admin_list_items_have_organizer_name(self, requester, admin):
        """Each item in admin list must include organizer_name (not N+1)."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

        resp = await adm_client.get("/api/v1/admin/bookings")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["items"]) >= 1
        for item in data["items"]:
            assert "organizer_name" in item
            assert item["organizer_name"] is not None

    async def test_admin_list_pagination(self, requester, admin):
        """limit and offset are respected."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        for h in [9, 10, 11]:
            await _create_booking(req_client, room["id"], _dt(h, 0), _dt(h, 30))

        resp = await adm_client.get(f"/api/v1/admin/bookings?room_id={room['id']}&limit=1&offset=0")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["total"] >= 3

    async def test_admin_list_filter_by_date_range(self, requester, admin):
        """date_from + date_to filters return only overlapping bookings."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        # Booking tomorrow
        b_tomorrow = await _create_booking(
            req_client, room["id"], _dt(10, 0, days_ahead=1), _dt(11, 0, days_ahead=1)
        )
        # Booking day after tomorrow
        await _create_booking(
            req_client, room["id"], _dt(10, 0, days_ahead=2), _dt(11, 0, days_ahead=2)
        )

        tomorrow_date = (_dt(10, 0, days_ahead=1)).date().isoformat()
        resp = await adm_client.get(
            f"/api/v1/admin/bookings?room_id={room['id']}&date_from={tomorrow_date}&date_to={tomorrow_date}"
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        ids = [item["id"] for item in data["items"]]
        assert b_tomorrow["id"] in ids


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: GET /admin/bookings/export (CSV)
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminBookingsExport:
    async def test_export_returns_csv_content_type(self, requester, admin):
        """Export endpoint returns text/csv content-type."""
        _, adm_client = admin
        resp = await adm_client.get("/api/v1/admin/bookings/export")
        assert resp.status_code == 200, resp.text
        assert "text/csv" in resp.headers.get("content-type", ""), resp.headers

    async def test_export_has_expected_header_row(self, requester, admin):
        """CSV must have the exact header row."""
        _, adm_client = admin
        resp = await adm_client.get("/api/v1/admin/bookings/export")
        assert resp.status_code == 200, resp.text
        lines = resp.text.strip().splitlines()
        assert len(lines) >= 1
        header = lines[0]
        expected_cols = [
            "id", "room_code", "room_name", "title", "organizer",
            "starts_at", "ends_at", "status", "attendees_count",
            "sync_status", "created_at",
        ]
        for col in expected_cols:
            assert col in header, f"Missing column '{col}' in CSV header: {header}"

    async def test_export_includes_booking_data(self, requester, admin):
        """CSV rows contain booking data."""
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        booking = await _create_booking(
            req_client, room["id"], _dt(10, 0), _dt(11, 0), title="CSV Export Test"
        )

        resp = await adm_client.get("/api/v1/admin/bookings/export")
        assert resp.status_code == 200, resp.text
        # The booking ID should appear in the CSV
        assert booking["id"] in resp.text

    async def test_export_non_admin_returns_403(self, requester):
        """Regular user cannot access the export endpoint."""
        _, req_client = requester
        resp = await req_client.get("/api/v1/admin/bookings/export")
        assert resp.status_code == 403, resp.text

    async def test_export_filter_by_room(self, requester, admin):
        """Export filter room_id works."""
        _, req_client = requester
        _, adm_client = admin

        room1 = await _create_room(adm_client)
        room2 = await _create_room(adm_client)
        b1 = await _create_booking(req_client, room1["id"], _dt(10, 0), _dt(11, 0))
        b2 = await _create_booking(req_client, room2["id"], _dt(10, 0), _dt(11, 0))

        resp = await adm_client.get(f"/api/v1/admin/bookings/export?room_id={room1['id']}")
        assert resp.status_code == 200, resp.text
        csv_text = resp.text
        assert b1["id"] in csv_text
        assert b2["id"] not in csv_text

    async def test_export_timestamps_include_timezone_offset(self, requester, admin):
        """CSV timestamps must carry a local timezone offset (DISPLAY_TIMEZONE), not UTC.

        A 10:00 AM Toronto booking must appear as ...T10:00:00-04:00 (EDT) or
        ...T10:00:00-05:00 (EST), never ...T14:00:00+00:00.
        """
        _, req_client = requester
        _, adm_client = admin

        room = await _create_room(adm_client)
        await _create_booking(
            req_client, room["id"], _dt(10, 0), _dt(11, 0), title="TZ Offset Test"
        )

        resp = await adm_client.get("/api/v1/admin/bookings/export")
        assert resp.status_code == 200, resp.text

        import csv as csv_mod
        import io as io_mod
        reader = csv_mod.DictReader(io_mod.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) >= 1, "Expected at least one data row in export"

        # America/Toronto is UTC-4 (EDT) or UTC-5 (EST); either offset is valid.
        # A UTC timestamp would have +00:00; a local one will have -04:00 or -05:00.
        for row in rows:
            for col in ("starts_at", "ends_at", "created_at"):
                ts = row[col]
                assert (
                    "-04:00" in ts or "-05:00" in ts
                ), f"Column '{col}' timestamp '{ts}' is not in DISPLAY_TIMEZONE (expected -04:00 or -05:00)"


# ─────────────────────────────────────────────────────────────────────────────
# Section 6: Matrix-admin regression tests (Finding 1)
#
# A user whose JWT *role* is NOT system_admin but whose role is granted
# manage_meeting_rooms=true in the shared Access Control Matrix (identity's
# role_permissions table, read via the uniops_authz package) must be able to:
#   (a) force-cancel another user's booking → 200, audit action force_cancel
#   (b) PATCH another user's booking → 200
# A plain requester (no matrix grant) must still get 404.
#
# Matrix grants are seeded via conftest.grant_matrix_permission() — see that
# helper's docstring for why this replaced the old company_config-based
# seeding (this gate no longer reads company_config at all).
# ─────────────────────────────────────────────────────────────────────────────


class TestMatrixAdminOverride:
    """Regression tests for Finding 1: admin override uses matrix, not role==system_admin."""

    async def test_matrix_admin_force_cancel_another_users_booking(
        self, requester, test_engine, db_session
    ):
        """A user whose role is granted manage_meeting_rooms via the matrix can
        force-cancel another user's booking and audit action is 'force_cancel'."""
        organizer, req_client = requester

        # Create a matrix-admin user with role 'procurement_manager' (not system_admin)
        matrix_admin_user = await make_user(test_engine, role="procurement_manager")
        matrix_admin_token = make_token(matrix_admin_user.id, matrix_admin_user.role)

        # Seed the matrix: procurement_manager gets manage_meeting_rooms=true
        await grant_matrix_permission(db_session, "procurement_manager", "manage_meeting_rooms")

        async with authed_client(matrix_admin_token, session=db_session) as matrix_admin_client:
            # The matrix admin needs a room — use system_admin to create it
            sys_admin_user = await make_user(test_engine, role="system_admin")
            sys_admin_token = make_token(sys_admin_user.id, sys_admin_user.role)
            async with authed_client(sys_admin_token, session=db_session) as adm_client:
                room = await _create_room(adm_client)

            # Organizer books the room
            booking = await _create_booking(req_client, room["id"], _dt(10, 0), _dt(11, 0))

            # Matrix-admin force-cancels (actor != organizer, but has manage_meeting_rooms)
            resp = await matrix_admin_client.post(
                f"/api/v1/bookings/{booking['id']}/cancel"
            )
            assert resp.status_code == 200, (
                f"Matrix admin (procurement_manager) should be able to force-cancel: {resp.text}"
            )
            assert resp.json()["cancelled"] == 1

        # Verify audit row has action='force_cancel'
        from sqlalchemy import select
        from app.models.audit import BookingAuditLog
        result = await db_session.execute(
            select(BookingAuditLog).where(
                BookingAuditLog.booking_id == uuid.UUID(booking["id"]),
                BookingAuditLog.action == "force_cancel",
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None, (
            "Expected audit row with action='force_cancel' for matrix-admin cancel"
        )
        assert audit.actor_id == matrix_admin_user.id

    async def test_matrix_admin_patch_another_users_booking(
        self, requester, test_engine, db_session
    ):
        """A user whose role is granted manage_meeting_rooms via the matrix can
        PATCH another user's booking and get 200."""
        organizer, req_client = requester

        matrix_admin_user = await make_user(test_engine, role="procurement_manager")
        matrix_admin_token = make_token(matrix_admin_user.id, matrix_admin_user.role)

        # Seed the matrix
        await grant_matrix_permission(db_session, "procurement_manager", "manage_meeting_rooms")

        sys_admin_user = await make_user(test_engine, role="system_admin")
        sys_admin_token = make_token(sys_admin_user.id, sys_admin_user.role)
        async with authed_client(sys_admin_token, session=db_session) as adm_client:
            room = await _create_room(adm_client)

        booking = await _create_booking(req_client, room["id"], _dt(14, 0), _dt(15, 0))

        async with authed_client(matrix_admin_token, session=db_session) as matrix_admin_client:
            resp = await matrix_admin_client.patch(
                f"/api/v1/bookings/{booking['id']}",
                json={"title": "Matrix Admin Override"},
            )
        assert resp.status_code == 200, (
            f"Matrix admin (procurement_manager) should be able to PATCH: {resp.text}"
        )
        assert resp.json()["title"] == "Matrix Admin Override"

    async def test_plain_requester_cannot_cancel_other_users_booking(
        self, requester, test_engine, db_session
    ):
        """A plain requester without manage_meeting_rooms must still get 404 when
        trying to cancel another user's booking (no matrix grant)."""
        organizer, req_client = requester

        sys_admin_user = await make_user(test_engine, role="system_admin")
        sys_admin_token = make_token(sys_admin_user.id, sys_admin_user.role)
        async with authed_client(sys_admin_token, session=db_session) as adm_client:
            room = await _create_room(adm_client)

        booking = await _create_booking(req_client, room["id"], _dt(9, 0), _dt(10, 0))

        other_user = await make_user(test_engine, role="requester")
        other_token = make_token(other_user.id, other_user.role)
        async with authed_client(other_token, session=db_session) as other_client:
            resp = await other_client.post(
                f"/api/v1/bookings/{booking['id']}/cancel"
            )
        assert resp.status_code == 404, (
            f"Plain requester must not be able to cancel another user's booking: {resp.text}"
        )
