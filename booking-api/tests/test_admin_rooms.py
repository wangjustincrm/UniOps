"""Tests for room admin CRUD, xlsx import, and module config endpoints.

TDD: Written BEFORE implementation — all tests must fail (ImportError or 404)
initially, then pass after implementation.
"""
import io
import uuid
from datetime import datetime, timedelta, timezone

import openpyxl
import pytest

from tests.conftest import make_token, authed_client, make_user


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_xlsx(rows: list[dict]) -> bytes:
    """Build a minimal xlsx workbook with the required import columns."""
    wb = openpyxl.Workbook()
    ws = wb.active
    headers = [
        "name", "code", "campus", "building", "floor", "area",
        "capacity", "equipment", "room_type",
        "open_time_start", "open_time_end",
    ]
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


ROOM_PAYLOAD = {
    "name": "Boardroom A",
    "code": "BR-A",
    "campus": "Main",
    "building": "HQ",
    "floor": "3",
    "area": "North",
    "capacity": 10,
    "equipment": ["projector", "whiteboard"],
    "room_type": "boardroom",
    "open_time_start": "08:00:00",
    "open_time_end": "20:00:00",
}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCreateRoom:
    async def test_create_room_returns_201_and_echoes_code(self, admin, test_engine):
        user, client = admin
        resp = await client.post("/api/v1/admin/rooms", json=ROOM_PAYLOAD)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["code"] == "BR-A"
        assert data["name"] == "Boardroom A"
        assert data["capacity"] == 10
        assert data["status"] == "available"
        assert "id" in data
        assert "created_at" in data

    async def test_duplicate_code_returns_409(self, admin, test_engine):
        user, client = admin
        payload = {**ROOM_PAYLOAD, "code": "DUP-01"}
        # first create succeeds
        r1 = await client.post("/api/v1/admin/rooms", json=payload)
        assert r1.status_code == 201, r1.text
        # second with same code → 409
        r2 = await client.post("/api/v1/admin/rooms", json=payload)
        assert r2.status_code == 409, r2.text

    async def test_capacity_zero_returns_422(self, admin, test_engine):
        user, client = admin
        payload = {**ROOM_PAYLOAD, "code": "CAP-ZERO", "capacity": 0}
        resp = await client.post("/api/v1/admin/rooms", json=payload)
        assert resp.status_code == 422, resp.text

    async def test_capacity_negative_returns_422(self, admin, test_engine):
        user, client = admin
        payload = {**ROOM_PAYLOAD, "code": "CAP-NEG", "capacity": -5}
        resp = await client.post("/api/v1/admin/rooms", json=payload)
        assert resp.status_code == 422, resp.text

    async def test_non_admin_returns_403(self, requester):
        user, client = requester
        resp = await client.post("/api/v1/admin/rooms", json=ROOM_PAYLOAD)
        assert resp.status_code == 403, resp.text


class TestGetRooms:
    async def test_get_rooms_returns_list(self, admin, test_engine):
        user, client = admin
        # create a room first
        payload = {**ROOM_PAYLOAD, "code": "LIST-01"}
        await client.post("/api/v1/admin/rooms", json=payload)
        resp = await client.get("/api/v1/admin/rooms")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)
        codes = [r["code"] for r in data]
        assert "LIST-01" in codes

    async def test_filter_by_status(self, admin, test_engine):
        user, client = admin
        payload = {**ROOM_PAYLOAD, "code": "FILT-STATUS"}
        cr = await client.post("/api/v1/admin/rooms", json=payload)
        assert cr.status_code == 201
        # filter available
        resp = await client.get("/api/v1/admin/rooms?status=available")
        assert resp.status_code == 200
        data = resp.json()
        assert all(r["status"] == "available" for r in data)

    async def test_non_admin_returns_403_on_list(self, requester):
        user, client = requester
        resp = await client.get("/api/v1/admin/rooms")
        assert resp.status_code == 403, resp.text


class TestPatchRoom:
    async def test_patch_room_updates_name(self, admin, test_engine):
        user, client = admin
        payload = {**ROOM_PAYLOAD, "code": "PATCH-01"}
        cr = await client.post("/api/v1/admin/rooms", json=payload)
        assert cr.status_code == 201
        room_id = cr.json()["id"]

        resp = await client.patch(f"/api/v1/admin/rooms/{room_id}", json={"name": "Updated Name"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Updated Name"
        assert resp.json()["code"] == "PATCH-01"  # unchanged

    async def test_patch_nonexistent_returns_404(self, admin):
        user, client = admin
        fake_id = str(uuid.uuid4())
        resp = await client.patch(f"/api/v1/admin/rooms/{fake_id}", json={"name": "X"})
        assert resp.status_code == 404, resp.text


class TestRoomStatusChange:
    async def test_status_change_to_maintenance(self, admin, test_engine):
        user, client = admin
        payload = {**ROOM_PAYLOAD, "code": "MAINT-01"}
        cr = await client.post("/api/v1/admin/rooms", json=payload)
        assert cr.status_code == 201
        room_id = cr.json()["id"]

        resp = await client.post(
            f"/api/v1/admin/rooms/{room_id}/status",
            json={"status": "maintenance", "notes": "Scheduled maintenance"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["room"]["status"] == "maintenance"
        assert "affected_future_bookings" in data
        assert isinstance(data["affected_future_bookings"], int)

    async def test_status_change_to_disabled(self, admin, test_engine):
        user, client = admin
        payload = {**ROOM_PAYLOAD, "code": "DISAB-01"}
        cr = await client.post("/api/v1/admin/rooms", json=payload)
        room_id = cr.json()["id"]
        resp = await client.post(
            f"/api/v1/admin/rooms/{room_id}/status",
            json={"status": "disabled", "notes": None},
        )
        assert resp.status_code == 200
        assert resp.json()["room"]["status"] == "disabled"

    async def test_status_change_counts_affected_future_bookings(self, admin, db_session):
        """Affected future bookings count = confirmed bookings with starts_at > now."""
        from app.models.booking import Booking

        user, client = admin

        # Create room via the admin HTTP client (bound to db_session)
        payload = {**ROOM_PAYLOAD, "code": f"CNT-FUT-{uuid.uuid4().hex[:4]}"}
        cr = await client.post("/api/v1/admin/rooms", json=payload)
        assert cr.status_code == 201
        room_id = uuid.UUID(cr.json()["id"])

        # Insert a future confirmed booking via the per-test db_session.
        # The client is bound to the same connection so it sees this row immediately.
        future_start = datetime.now(timezone.utc) + timedelta(days=1)
        future_end = future_start + timedelta(hours=1)
        booking = Booking(
            room_id=room_id,
            title="Future Meeting",
            organizer_id=user.id,
            attendee_ids=[],
            starts_at=future_start,
            ends_at=future_end,
            status="confirmed",
            calendar_uid=f"test-{uuid.uuid4()}@booking-test",
        )
        db_session.add(booking)
        await db_session.flush()

        resp = await client.post(
            f"/api/v1/admin/rooms/{room_id}/status",
            json={"status": "maintenance", "notes": None},
        )
        assert resp.status_code == 200
        assert resp.json()["affected_future_bookings"] >= 1

    async def test_invalid_status_returns_422(self, admin, test_engine):
        user, client = admin
        payload = {**ROOM_PAYLOAD, "code": "INV-STATUS"}
        cr = await client.post("/api/v1/admin/rooms", json=payload)
        room_id = cr.json()["id"]
        resp = await client.post(
            f"/api/v1/admin/rooms/{room_id}/status",
            json={"status": "bogus"},
        )
        assert resp.status_code == 422, resp.text

    async def test_non_admin_status_change_returns_403(self, requester, test_engine):
        user, client = requester
        fake_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/admin/rooms/{fake_id}/status",
            json={"status": "maintenance"},
        )
        assert resp.status_code == 403, resp.text


class TestXlsxImport:
    async def test_xlsx_import_happy_path(self, admin, test_engine):
        """2 valid rows + 1 bad capacity row → created=2, errors=[{row:4,...}]"""
        user, client = admin
        rows = [
            {
                "name": "Room Alpha", "code": "IMP-A", "campus": "Main",
                "building": "HQ", "floor": "1", "area": "East",
                "capacity": 6, "equipment": "projector,whiteboard",
                "room_type": "standard",
                "open_time_start": "08:00", "open_time_end": "18:00",
            },
            {
                "name": "Room Beta", "code": "IMP-B", "campus": "Main",
                "building": "HQ", "floor": "2", "area": "West",
                "capacity": 12, "equipment": "",
                "room_type": "training",
                "open_time_start": "", "open_time_end": "",
            },
            {
                # bad capacity: 0
                "name": "Bad Room", "code": "IMP-BAD", "campus": "",
                "building": "", "floor": "", "area": "",
                "capacity": 0, "equipment": "",
                "room_type": "standard",
                "open_time_start": "", "open_time_end": "",
            },
        ]
        xlsx_bytes = _make_xlsx(rows)
        resp = await client.post(
            "/api/v1/admin/rooms/import",
            content=xlsx_bytes,
            headers={"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["created"] == 2
        assert len(data["errors"]) == 1
        assert data["errors"][0]["row"] == 4  # header=1, data rows 2,3,4

    async def test_xlsx_import_non_admin_returns_403(self, requester):
        user, client = requester
        xlsx_bytes = _make_xlsx([])
        resp = await client.post(
            "/api/v1/admin/rooms/import",
            content=xlsx_bytes,
            headers={"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        )
        assert resp.status_code == 403, resp.text

    async def test_xlsx_import_duplicate_code_is_error_not_crash(self, admin, test_engine):
        """A row whose code already exists goes into errors, not a 500."""
        user, client = admin
        # pre-create a room with code IMP-DUP
        await client.post("/api/v1/admin/rooms", json={**ROOM_PAYLOAD, "code": "IMP-DUP"})
        rows = [
            {
                "name": "Dup Room", "code": "IMP-DUP", "campus": "Main",
                "building": "HQ", "floor": "1", "area": "East",
                "capacity": 5, "equipment": "",
                "room_type": "standard",
                "open_time_start": "", "open_time_end": "",
            },
        ]
        xlsx_bytes = _make_xlsx(rows)
        resp = await client.post(
            "/api/v1/admin/rooms/import",
            content=xlsx_bytes,
            headers={"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert len(data["errors"]) == 1


class TestAdminConfig:
    async def test_get_config_returns_defaults(self, admin):
        user, client = admin
        resp = await client.get("/api/v1/admin/config")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "smtp_settings" in data
        assert "rules" in data
        assert "organizer_mode" in data
        # default rules should have slot_minutes
        assert data["rules"].get("slot_minutes") == 15

    async def test_put_config_updates_organizer_mode(self, admin):
        user, client = admin
        resp = await client.put(
            "/api/v1/admin/config",
            json={"organizer_mode": "initiator"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["organizer_mode"] == "initiator"

    async def test_put_config_updates_rules(self, admin):
        user, client = admin
        # First, ensure a known baseline for slot_minutes
        await client.put("/api/v1/admin/config", json={"rules": {"slot_minutes": 15}})

        # Send a partial update that only touches advance_days.
        resp = await client.put(
            "/api/v1/admin/config",
            json={"rules": {"advance_days": 14}},
        )
        assert resp.status_code == 200
        rules = resp.json()["rules"]
        # The updated key must be present.
        assert rules["advance_days"] == 14
        # The untouched key must survive (deep-merge semantics).
        assert rules.get("slot_minutes") == 15

        # A second partial update should still preserve other keys.
        resp2 = await client.put(
            "/api/v1/admin/config",
            json={"rules": {"slot_minutes": 30}},
        )
        assert resp2.status_code == 200
        rules2 = resp2.json()["rules"]
        assert rules2["slot_minutes"] == 30
        assert rules2.get("advance_days") == 14

    async def test_non_admin_config_returns_403(self, requester):
        user, client = requester
        resp = await client.get("/api/v1/admin/config")
        assert resp.status_code == 403, resp.text
