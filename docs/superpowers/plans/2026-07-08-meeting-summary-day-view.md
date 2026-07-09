# Meeting Summary Day View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `GET /bookings/day` endpoint and a `MeetingSummaryPage` frontend showing all rooms' bookings in an Outlook-Day-View-style grid for a selected calendar day.

**Architecture:** Backend — a new route declared before the `/{booking_id}` parameterized routes in `app/api/v1/bookings.py`; schema `DaySummaryOut` added to `app/schemas/booking.py`; organizer names resolved via a single bulk JOIN (no N+1). Frontend — new TypeScript types in `lib/types.ts`, a `daySummary` method on `bookingService` + `useDaySummary` hook in `services/api.ts`, and a new `MeetingSummaryPage.tsx` that renders a CSS grid/absolute-position Outlook-style day timeline with a red current-time indicator and per-room booking blocks.

**Tech Stack:** Python 3.12 + FastAPI + SQLAlchemy async, pytest-asyncio; React 18 + TypeScript 6 + TanStack Query v5, Tailwind CSS, Lucide icons.

---

## File Map

| File | Change |
|---|---|
| `booking-api/app/schemas/booking.py` | Add `DaySummaryRoom`, `DaySummaryBookingOut`, `DaySummaryOut` |
| `booking-api/app/api/v1/bookings.py` | Add `GET /day` route (declared before `/{booking_id}` routes) |
| `booking-api/tests/test_day_summary.py` | New test file |
| `booking/src/lib/types.ts` | Add `DaySummaryRoom`, `DaySummaryBooking`, `DaySummaryOut` |
| `booking/src/services/api.ts` | Add `bookingService.daySummary()` + `useDaySummary()` hook |
| `booking/src/pages/MeetingSummaryPage.tsx` | New page |
| `booking/src/app/routes.tsx` | Register `/summary` route |
| `booking/src/components/layout/AppLayout.tsx` | Add "Meeting Summary" nav item |

---

## Task 1: Backend schemas — `DaySummaryOut`

**Files:**
- Modify: `booking-api/app/schemas/booking.py`

- [ ] **Step 1: Add the three new schema classes** at the bottom of `booking-api/app/schemas/booking.py` (after `AdminBookingListOut`):

```python
# ─────────────────────────────────────────────────────────────────────────────
# Day-summary schemas  (GET /bookings/day)
# ─────────────────────────────────────────────────────────────────────────────

class DaySummaryRoom(BaseModel):
    """Room row in the day-summary response."""
    id: uuid.UUID
    name: str
    code: str
    floor: str | None
    area: str | None
    capacity: int
    status: str  # "available" | "maintenance" (disabled rooms excluded by query)

    model_config = ConfigDict(from_attributes=True)


class DaySummaryBookingOut(BookingSlimOut):
    """BookingSlimOut extended with room_id for the day-summary grid."""
    room_id: uuid.UUID

    model_config = ConfigDict(from_attributes=True)


class DaySummaryOut(BaseModel):
    """Response from GET /bookings/day."""
    date: str                       # "YYYY-MM-DD"
    open_start: str                 # "HH:MM"  from config defaults
    open_end: str                   # "HH:MM"
    rooms: list[DaySummaryRoom]     # non-disabled rooms, sorted floor then name
    bookings: list[DaySummaryBookingOut]  # confirmed bookings in the day window
```

- [ ] **Step 2: Verify the schema file parses**

Run from `booking-api/`:
```
.venv/Scripts/python.exe -c "from app.schemas.booking import DaySummaryOut, DaySummaryRoom, DaySummaryBookingOut; print('OK')"
```
Expected output: `OK`

---

## Task 2: Backend — `GET /bookings/day` route

**Files:**
- Modify: `booking-api/app/api/v1/bookings.py`

The route must be declared **before** the `/{booking_id}` parameterized routes. In the existing file, the first parameterized route is `PATCH /{booking_id}` at line ~621. Insert the new route before that block.

- [ ] **Step 1: Add the new route** — insert this block in `booking-api/app/api/v1/bookings.py` immediately before the `# ─── PATCH /bookings/{id} ───` comment block (currently around line 619):

```python
# ─────────────────────────────────────────────────────────────────────────────
# GET /bookings/day
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/day", response_model=DaySummaryOut)
async def get_day_summary(
    db: SessionDep,
    current_user: CurrentUser,
    date: str = Query(..., description="Calendar date in YYYY-MM-DD format (interpreted in DISPLAY_TIMEZONE)"),
):
    """Return all rooms and confirmed bookings for a single calendar day.

    `date` is a local calendar day in settings.DISPLAY_TIMEZONE.
    The query window is [date 00:00 local, next day 00:00 local) converted to UTC.
    Rooms with status 'disabled' are excluded; maintenance rooms are included
    (frontend renders them greyed out).
    Organizer names are resolved via a single bulk JOIN (no N+1).
    """
    from datetime import date as date_type
    from app.schemas.booking import DaySummaryOut, DaySummaryRoom, DaySummaryBookingOut

    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)

    # Parse date string
    try:
        parsed_date = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date must be in YYYY-MM-DD format",
        )

    # Local-day window → UTC
    day_start_local = datetime(parsed_date.year, parsed_date.month, parsed_date.day, 0, 0, 0, tzinfo=tz)
    day_end_local = day_start_local + timedelta(days=1)
    day_start_utc = day_start_local.astimezone(timezone.utc)
    day_end_utc = day_end_local.astimezone(timezone.utc)

    # Config defaults for open hours
    config = await get_or_create_config(db)
    cfg_rules: dict = config.rules or {}
    open_start = cfg_rules.get("default_open_start", "08:00")
    open_end = cfg_rules.get("default_open_end", "20:00")

    # All non-disabled rooms, sorted by floor then name
    rooms_result = await db.execute(
        select(MeetingRoom)
        .where(MeetingRoom.status != "disabled")
        .order_by(MeetingRoom.floor.nulls_last(), MeetingRoom.name)
    )
    rooms = list(rooms_result.scalars().all())

    # Confirmed bookings overlapping the day window, joined with organizer names
    bookings_result = await db.execute(
        select(Booking, User.full_name)
        .join(User, Booking.organizer_id == User.id, isouter=True)
        .where(
            Booking.status == "confirmed",
            Booking.starts_at < day_end_utc,
            Booking.ends_at > day_start_utc,
        )
    )
    booking_rows = bookings_result.all()

    return DaySummaryOut(
        date=parsed_date.isoformat(),
        open_start=open_start,
        open_end=open_end,
        rooms=[
            DaySummaryRoom(
                id=r.id,
                name=r.name,
                code=r.code,
                floor=r.floor,
                area=r.area,
                capacity=r.capacity,
                status=r.status,
            )
            for r in rooms
        ],
        bookings=[
            DaySummaryBookingOut(
                id=b.id,
                title=b.title,
                starts_at=b.starts_at,
                ends_at=b.ends_at,
                organizer_name=organizer_name or "Unknown",
                room_id=b.room_id,
            )
            for b, organizer_name in booking_rows
        ],
    )
```

- [ ] **Step 2: Add missing import** — `timezone` is used in the route. Check the existing imports at the top of `bookings.py` — `datetime` and `timedelta` are imported, but `timezone` may not be. Add it to the `from datetime import ...` line:

The current import line is:
```python
from datetime import datetime, time, timedelta
```
Change it to:
```python
from datetime import datetime, time, timedelta, timezone
```

- [ ] **Step 3: Verify the app starts**

```
cd booking-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 POSTGRES_DB=epms JWT_SECRET_KEY=test-secret SCHEDULER_ENABLED=false .venv/Scripts/python.exe -c "from app.main import create_app; app = create_app(); print('OK')"
```
Expected: `OK`

---

## Task 3: Backend tests — `test_day_summary.py`

**Files:**
- Create: `booking-api/tests/test_day_summary.py`
- Test: all scenarios run as part of the full suite

The test strategy:
- Seed 2 rooms (one available, one maintenance) and 1 disabled room.
- Seed bookings: one on the target day in room A, one in room B on the target day, one cancelled (excluded), one the NEXT calendar day (excluded), and one that crosses UTC midnight but lands on the LOCAL target day.
- Verify: disabled room absent from `rooms[]`; maintenance room present (with status "maintenance"); cancelled booking absent; next-day booking absent; local-day boundary booking present.

- [ ] **Step 1: Write `booking-api/tests/test_day_summary.py`**

```python
"""TDD tests for GET /bookings/day (day-summary endpoint).

Covers:
  - rooms[]: non-disabled rooms sorted floor/name; disabled excluded; maintenance included
  - bookings[]: confirmed bookings overlapping local day window only
  - cancelled bookings excluded
  - next-day booking (local time) excluded
  - local-day boundary: booking at 23:00 local time on target day → included
  - UTC-midnight cross: booking starts before UTC midnight but is on the next local day → excluded
  - response shape: date, open_start, open_end, rooms, bookings
  - bad date format → 422
  - unauthenticated → 403
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
        # 10:00–11:00 local on target day
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
        # 09:00–10:00 local on the NEXT day
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
        the LOCAL calendar day window [Oct 5 00:00–Oct 6 00:00 local).
        """
        _, adm = admin
        _, req = requester
        room = await _create_room(adm)
        target = "2026-10-05"
        # 23:00–23:45 local → well inside the local day but in the next UTC day
        starts = _local(2026, 10, 5, 23, 0)
        ends   = _local(2026, 10, 5, 23, 45)
        await _insert_booking(db_session, room["id"], starts, ends)

        resp = await req.get("/api/v1/bookings/day", params={"date": target})
        assert resp.status_code == 200
        bookings = resp.json()["bookings"]
        matching = [b for b in bookings if b["room_id"] == room["id"]]
        assert len(matching) == 1, "Late-night local booking must be included in the correct day"

    async def test_utc_midnight_cross_not_on_local_day_excluded(self, admin, requester, db_session):
        """A booking that starts before UTC midnight but is on the NEXT local day is excluded.

        2026-10-06 01:00 local (EDT, UTC-4) = 2026-10-06 05:00 UTC.
        Querying for 2026-10-05 must NOT return this booking.
        """
        _, adm = admin
        _, req = requester
        room = await _create_room(adm)
        target = "2026-10-05"
        # 01:00 local on Oct 6 — next local day
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
```

- [ ] **Step 2: Run only the new tests (they should FAIL — route not yet declared at this point in the task sequence)**

```
cd booking-api
$env:POSTGRES_HOST="localhost"; $env:POSTGRES_PORT="5432"; $env:POSTGRES_USER="epms"; $env:POSTGRES_PASSWORD="7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9"; $env:POSTGRES_DB="epms"; $env:JWT_SECRET_KEY="test-secret"; $env:SCHEDULER_ENABLED="false"
.venv/Scripts/python.exe -m pytest tests/test_day_summary.py -q 2>&1 | tail -5
```

> Note: If Task 2 (route implementation) was already done before Task 3 in execution order, skip this "verify fail" step and go straight to the green run in Task 4.

---

## Task 4: Run the full test suite

**Files:** (none modified — this is a verification step)

- [ ] **Step 1: Run ALL tests**

```
cd booking-api
$env:POSTGRES_HOST="localhost"; $env:POSTGRES_PORT="5432"; $env:POSTGRES_USER="epms"; $env:POSTGRES_PASSWORD="7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9"; $env:POSTGRES_DB="epms"; $env:JWT_SECRET_KEY="test-secret"; $env:SCHEDULER_ENABLED="false"
.venv/Scripts/python.exe -m pytest tests -q 2>&1 | tail -20
```

Expected: all prior tests green + all new `test_day_summary` tests green. Count must be ≥ 264 + new test count (≥ 278).

- [ ] **Step 2: Commit the backend changes**

```
git add booking-api/app/schemas/booking.py booking-api/app/api/v1/bookings.py booking-api/tests/test_day_summary.py
git commit -m "feat(booking-api): GET /bookings/day day-summary endpoint with TDD tests"
```

---

## Task 5: Frontend types

**Files:**
- Modify: `booking/src/lib/types.ts`

- [ ] **Step 1: Add three new types** at the end of `booking/src/lib/types.ts` (after the `NotificationListOut` interface):

```typescript
// ── Day Summary ───────────────────────────────────────────────────────────────

export interface DaySummaryRoom {
  id: string
  name: string
  code: string
  floor: string | null
  area: string | null
  capacity: number
  status: 'available' | 'maintenance'
}

/** BookingSlimOut extended with room_id for the day-summary grid. */
export interface DaySummaryBooking extends BookingSlimOut {
  room_id: string
}

export interface DaySummaryOut {
  date: string          // "YYYY-MM-DD"
  open_start: string    // "HH:MM"
  open_end: string      // "HH:MM"
  rooms: DaySummaryRoom[]
  bookings: DaySummaryBooking[]
}
```

- [ ] **Step 2: Verify types parse — quick tsc check**

```
cd booking
npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | head -20
```

Expected: no errors (or only pre-existing errors unrelated to the new types).

---

## Task 6: Frontend service + hook

**Files:**
- Modify: `booking/src/services/api.ts`

- [ ] **Step 1: Add the import** — `DaySummaryOut` needs to be imported at the top of `services/api.ts`. The current import from `@/lib/types` is on line 15–29. Add `DaySummaryOut` to that import block:

Find:
```typescript
import type {
  AdminBookingFilters,
  AdminBookingListOut,
  AdminConfig,
  BookingAdminOut,
  BookingOut,
  BookingSlimOut,
  DirectoryUserOut,
  ImportResult,
  NotificationListOut,
  NotificationLogOut,
  RoomCreate,
  RoomDetailOut,
  RoomOut,
  RoomUpdate,
  RoomWithStatusOut,
  SeriesSpec,
  StatusChangeOut,
  SuggestOut,
} from '@/lib/types'
```

Replace with:
```typescript
import type {
  AdminBookingFilters,
  AdminBookingListOut,
  AdminConfig,
  BookingAdminOut,
  BookingOut,
  BookingSlimOut,
  DaySummaryOut,
  DirectoryUserOut,
  ImportResult,
  NotificationListOut,
  NotificationLogOut,
  RoomCreate,
  RoomDetailOut,
  RoomOut,
  RoomUpdate,
  RoomWithStatusOut,
  SeriesSpec,
  StatusChangeOut,
  SuggestOut,
} from '@/lib/types'
```

- [ ] **Step 2: Add `daySummary` to `bookingService`** — find the `bookingService` object in `services/api.ts` (currently ending with `cancel`). Add `daySummary` as a new method:

Find (near the end of `bookingService`):
```typescript
  cancel(id: string, series = false) {
    const qs = series ? '?series=true' : ''
    return api.post<{ cancelled: number }>(`/api/v1/bookings/${id}/cancel${qs}`, {})
  },
}
```

Replace with:
```typescript
  cancel(id: string, series = false) {
    const qs = series ? '?series=true' : ''
    return api.post<{ cancelled: number }>(`/api/v1/bookings/${id}/cancel${qs}`, {})
  },

  daySummary(date: string) {
    return api.get<DaySummaryOut>(`/api/v1/bookings/day?date=${encodeURIComponent(date)}`)
  },
}
```

- [ ] **Step 3: Add the `useDaySummary` hook** — add this after the `usePrecheckBooking` export function (around line 249):

```typescript
export function useDaySummary(date: string) {
  return useQuery<DaySummaryOut>({
    queryKey: ['booking-day-summary', date],
    queryFn: () => bookingService.daySummary(date),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
}
```

- [ ] **Step 4: Typecheck**

```
cd booking
npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | head -20
```

Expected: no new errors.

---

## Task 7: `MeetingSummaryPage.tsx`

**Files:**
- Create: `booking/src/pages/MeetingSummaryPage.tsx`

This is the largest piece. Build it in one shot — every helper is defined within the same file. The page:

1. **Header bar:** "Today" button, ‹ › arrows, native `<input type="date">`, weekday label.
2. **Grid:** sticky time-gutter (left column, hourly labels), horizontal-scroll room columns (min-width 180 px each), sticky room headers.
3. **Booking blocks:** absolutely positioned within the room column, sized top/height from starts_at/ends_at relative to the open span; teal style; title + time + organizer. Click room header → navigate to `/rooms/${id}`.
4. **Current-time indicator:** red horizontal line across all room columns, visible only when selected date is today; recomputed every 60 s via `setInterval`.
5. **Empty states:** no rooms hint; rooms-but-no-bookings subtle text.

- [ ] **Step 1: Create `booking/src/pages/MeetingSummaryPage.tsx`**

```tsx
import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronLeft, ChevronRight, CalendarDays } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useDaySummary } from '@/services/api'
import type { DaySummaryRoom, DaySummaryBooking } from '@/lib/types'

// ── Date helpers ──────────────────────────────────────────────────────────────

function todayIso(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00`)
  d.setDate(d.getDate() + n)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function formatWeekday(iso: string): string {
  const d = new Date(`${iso}T00:00:00`)
  return d.toLocaleDateString('en-CA', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })
}

// ── Time helpers ──────────────────────────────────────────────────────────────

/** Parse "HH:MM" → total minutes since midnight. */
function hhmm2min(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

/** Convert a UTC ISO datetime string to local HH:MM. */
function toLocalHHMM(utcIso: string): string {
  const d = new Date(utcIso)
  return d.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit', hour12: false })
}

/** Convert a UTC ISO datetime to local minutes-since-midnight. */
function toLocalMin(utcIso: string): number {
  const d = new Date(utcIso)
  return d.getHours() * 60 + d.getMinutes()
}

/** Current time as local minutes-since-midnight. */
function nowLocalMin(): number {
  const d = new Date()
  return d.getHours() * 60 + d.getMinutes()
}

// ── Grid constants ─────────────────────────────────────────────────────────────

const GUTTER_W = 56     // px — left hour label column
const ROOM_MIN_W = 180  // px — minimum room column width
const ROW_H = 64        // px per hour

// ── Room header ───────────────────────────────────────────────────────────────

function RoomHeader({ room, onClick }: { room: DaySummaryRoom; onClick: () => void }) {
  const isMaint = room.status === 'maintenance'
  return (
    <div
      onClick={onClick}
      className={cn(
        'flex flex-col gap-0.5 px-3 py-2 border-b border-r border-neutral-200 cursor-pointer select-none',
        'hover:bg-neutral-50 transition-colors',
        isMaint && 'bg-neutral-100',
      )}
      title={isMaint ? `${room.name} — Maintenance` : room.name}
    >
      <div className="flex items-center gap-1.5 min-w-0">
        <span className={cn('text-sm font-semibold truncate', isMaint ? 'text-neutral-400' : 'text-neutral-800')}>
          {room.name}
        </span>
        {isMaint && (
          <span className="shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 ring-1 ring-inset ring-amber-200">
            Maintenance
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 text-[11px] text-neutral-400">
        <span>{room.code}</span>
        <span>·</span>
        <span>{room.capacity} pax</span>
      </div>
    </div>
  )
}

// ── Booking block ─────────────────────────────────────────────────────────────

interface BookingBlockProps {
  booking: DaySummaryBooking
  openStartMin: number
  openSpanMin: number
}

function BookingBlock({ booking, openStartMin, openSpanMin }: BookingBlockProps) {
  const startMin = toLocalMin(booking.starts_at)
  const endMin   = toLocalMin(booking.ends_at)

  // Clamp to the visible span
  const clampedStart = Math.max(startMin, openStartMin)
  const clampedEnd   = Math.min(endMin, openStartMin + openSpanMin)

  if (clampedEnd <= clampedStart) return null

  const topPct    = ((clampedStart - openStartMin) / openSpanMin) * 100
  const heightPct = ((clampedEnd - clampedStart) / openSpanMin) * 100

  const startLabel = toLocalHHMM(booking.starts_at)
  const endLabel   = toLocalHHMM(booking.ends_at)
  const tooltip    = `${booking.title}\n${startLabel}–${endLabel}\n${booking.organizer_name}`

  return (
    <div
      title={tooltip}
      style={{ top: `${topPct}%`, height: `${heightPct}%` }}
      className={cn(
        'absolute inset-x-1 rounded-sm border-l-[3px] border-[#085E5E] bg-[#085E5E]/10 px-1.5 py-0.5',
        'overflow-hidden select-none',
      )}
    >
      <p className="truncate text-[11px] font-semibold text-[#085E5E] leading-tight">{booking.title}</p>
      <p className="truncate text-[10px] text-[#085E5E]/70 leading-tight">{startLabel}–{endLabel}</p>
      <p className="truncate text-[10px] text-[#085E5E]/60 leading-tight">{booking.organizer_name}</p>
    </div>
  )
}

// ── Current time indicator ────────────────────────────────────────────────────

interface CurrentTimeLineProps {
  openStartMin: number
  openSpanMin: number
}

function CurrentTimeLine({ openStartMin, openSpanMin }: CurrentTimeLineProps) {
  const [currentMin, setCurrentMin] = useState(nowLocalMin)

  useEffect(() => {
    const id = setInterval(() => setCurrentMin(nowLocalMin()), 60_000)
    return () => clearInterval(id)
  }, [])

  if (currentMin < openStartMin || currentMin > openStartMin + openSpanMin) return null

  const topPct = ((currentMin - openStartMin) / openSpanMin) * 100

  return (
    <div
      className="absolute left-0 right-0 z-20 flex items-center pointer-events-none"
      style={{ top: `${topPct}%` }}
    >
      <div className="h-2 w-2 rounded-full bg-red-500 -translate-x-1 shrink-0" />
      <div className="flex-1 border-t-2 border-red-500" />
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function MeetingSummaryPage() {
  const [selectedDate, setSelectedDate] = useState(todayIso)
  const navigate = useNavigate()
  const isToday = selectedDate === todayIso()

  const { data, isLoading, isError } = useDaySummary(selectedDate)

  // Computed from API response (or defaults while loading)
  const openStartMin = data ? hhmm2min(data.open_start) : hhmm2min('08:00')
  const openEndMin   = data ? hhmm2min(data.open_end)   : hhmm2min('20:00')
  const openSpanMin  = openEndMin - openStartMin

  // Hour ticks for the gutter
  const startHour = Math.floor(openStartMin / 60)
  const endHour   = Math.ceil(openEndMin / 60)
  const hours: number[] = []
  for (let h = startHour; h <= endHour; h++) hours.push(h)

  // Bookings indexed by room_id
  const bookingsByRoom = useCallback((): Map<string, DaySummaryBooking[]> => {
    const map = new Map<string, DaySummaryBooking[]>()
    if (!data) return map
    for (const b of data.bookings) {
      const list = map.get(b.room_id) ?? []
      list.push(b)
      map.set(b.room_id, list)
    }
    return map
  }, [data])()

  const gridH = (openSpanMin / 60) * ROW_H

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* ── Header bar ─────────────────────────────────────────────────────── */}
      <div className="no-print flex shrink-0 items-center gap-3 border-b border-neutral-200 bg-white px-4 py-3">
        <CalendarDays className="h-5 w-5 text-[#085E5E]" />
        <h1 className="text-base font-semibold text-neutral-800">Meeting Summary</h1>

        <div className="mx-2 h-5 w-px bg-neutral-200" />

        {/* Today button */}
        <button
          onClick={() => setSelectedDate(todayIso())}
          className={cn(
            'rounded-md border px-3 py-1 text-sm font-medium transition-colors',
            isToday
              ? 'border-[#085E5E] bg-[#085E5E] text-white'
              : 'border-neutral-300 bg-white text-neutral-700 hover:bg-neutral-50',
          )}
        >
          Today
        </button>

        {/* Arrows */}
        <button
          onClick={() => setSelectedDate(d => addDays(d, -1))}
          className="rounded p-1 text-neutral-500 hover:bg-neutral-100 transition-colors"
          aria-label="Previous day"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <button
          onClick={() => setSelectedDate(d => addDays(d, 1))}
          className="rounded p-1 text-neutral-500 hover:bg-neutral-100 transition-colors"
          aria-label="Next day"
        >
          <ChevronRight className="h-4 w-4" />
        </button>

        {/* Date input */}
        <input
          type="date"
          value={selectedDate}
          onChange={e => e.target.value && setSelectedDate(e.target.value)}
          className="rounded-md border border-neutral-300 px-2 py-1 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-[#085E5E]/40"
        />

        {/* Weekday label */}
        <span className="text-sm text-neutral-500">{formatWeekday(selectedDate)}</span>
      </div>

      {/* ── Loading / error states ─────────────────────────────────────────── */}
      {isLoading && (
        <div className="flex flex-1 items-center justify-center text-sm text-neutral-400">
          Loading schedule…
        </div>
      )}
      {isError && (
        <div className="flex flex-1 items-center justify-center text-sm text-red-500">
          Failed to load schedule. Please try again.
        </div>
      )}

      {/* ── No rooms ──────────────────────────────────────────────────────── */}
      {data && data.rooms.length === 0 && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-sm text-neutral-400">
          <CalendarDays className="h-8 w-8 opacity-30" />
          <p>No meeting rooms found.</p>
          <p className="text-xs">Ask an admin to configure rooms in Rooms Admin.</p>
        </div>
      )}

      {/* ── Day grid ──────────────────────────────────────────────────────── */}
      {data && data.rooms.length > 0 && (
        <div className="relative flex-1 overflow-auto">
          {/* Sticky room header row */}
          <div
            className="sticky top-0 z-10 flex bg-white"
            style={{ paddingLeft: GUTTER_W }}
          >
            {data.rooms.map(room => (
              <div
                key={room.id}
                style={{ minWidth: ROOM_MIN_W, width: ROOM_MIN_W }}
                className="shrink-0"
              >
                <RoomHeader room={room} onClick={() => navigate(`/rooms/${room.id}`)} />
              </div>
            ))}
          </div>

          {/* Scroll body */}
          <div className="flex" style={{ minWidth: GUTTER_W + data.rooms.length * ROOM_MIN_W }}>
            {/* Time gutter */}
            <div
              className="sticky left-0 z-10 shrink-0 bg-white border-r border-neutral-200"
              style={{ width: GUTTER_W, height: gridH }}
            >
              {hours.map(h => {
                const topPct = ((h * 60 - openStartMin) / openSpanMin) * 100
                if (topPct < 0 || topPct > 100) return null
                return (
                  <div
                    key={h}
                    className="absolute right-2 -translate-y-2 text-[10px] text-neutral-400 select-none"
                    style={{ top: `${topPct}%` }}
                  >
                    {String(h).padStart(2, '0')}:00
                  </div>
                )
              })}
            </div>

            {/* Room columns */}
            <div className="relative flex flex-1" style={{ height: gridH }}>
              {/* Hour grid lines (shared background) */}
              {hours.map(h => {
                const topPct = ((h * 60 - openStartMin) / openSpanMin) * 100
                if (topPct < 0 || topPct > 100) return null
                return (
                  <div
                    key={h}
                    className="absolute left-0 right-0 border-t border-neutral-200"
                    style={{ top: `${topPct}%` }}
                  />
                )
              })}
              {/* 30-min half-hour lines */}
              {hours.map(h => {
                const topPct = ((h * 60 + 30 - openStartMin) / openSpanMin) * 100
                if (topPct <= 0 || topPct >= 100) return null
                return (
                  <div
                    key={`${h}-30`}
                    className="absolute left-0 right-0 border-t border-neutral-100"
                    style={{ top: `${topPct}%` }}
                  />
                )
              })}

              {/* Current time indicator across all room columns */}
              {isToday && (
                <CurrentTimeLine openStartMin={openStartMin} openSpanMin={openSpanMin} />
              )}

              {/* Per-room column */}
              {data.rooms.map((room, idx) => {
                const roomBookings = bookingsByRoom.get(room.id) ?? []
                const isMaint = room.status === 'maintenance'
                return (
                  <div
                    key={room.id}
                    className={cn(
                      'relative shrink-0 border-r border-neutral-200',
                      isMaint && 'bg-neutral-50/60',
                    )}
                    style={{ width: ROOM_MIN_W, height: '100%' }}
                  >
                    {/* No-meetings overlay (only when no bookings and room is available) */}
                    {roomBookings.length === 0 && !isMaint && data.bookings.length === 0 && idx === 0 && (
                      <div className="absolute inset-0 flex items-center justify-center">
                        <span className="text-[11px] text-neutral-300 select-none">No meetings scheduled</span>
                      </div>
                    )}
                    {roomBookings.map(b => (
                      <BookingBlock
                        key={b.id}
                        booking={b}
                        openStartMin={openStartMin}
                        openSpanMin={openSpanMin}
                      />
                    ))}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Typecheck after creating the page**

```
cd booking
npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | head -30
```

Expected: no errors from the new file.

---

## Task 8: Wire routes and nav

**Files:**
- Modify: `booking/src/app/routes.tsx`
- Modify: `booking/src/components/layout/AppLayout.tsx`

### 8A — routes.tsx

- [ ] **Step 1: Add the import** at the top of `booking/src/app/routes.tsx`:

```typescript
import MeetingSummaryPage from '@/pages/MeetingSummaryPage'
```

- [ ] **Step 2: Insert the route** between `/rooms` and `/my` routes in `bookingRoutes`. The current array starts:

```typescript
export const bookingRoutes: RouteDef[] = [
  { path: '/rooms',               element: <RoomsPage />,             tab: { title: 'Rooms',          icon: 'DoorOpen',    keyStrategy: 'static', pinned: true } },
  { path: '/rooms/:id/book',      element: <BookingCreatePage />,     tab: { title: (p) => `Book ${p.id}`, icon: 'CalendarPlus', keyStrategy: 'param', paramName: 'id' } },
  { path: '/rooms/:id',           element: <RoomDetailPage />,        tab: { title: (p) => `Room ${p.id}`, icon: 'DoorOpen',     keyStrategy: 'param', paramName: 'id' } },
  { path: '/my',                  element: <MyBookingsPage />,        tab: { title: 'My Bookings',    icon: 'CalendarDays', keyStrategy: 'static' } },
```

Replace with:
```typescript
export const bookingRoutes: RouteDef[] = [
  { path: '/rooms',               element: <RoomsPage />,             tab: { title: 'Rooms',          icon: 'DoorOpen',    keyStrategy: 'static', pinned: true } },
  { path: '/rooms/:id/book',      element: <BookingCreatePage />,     tab: { title: (p) => `Book ${p.id}`, icon: 'CalendarPlus', keyStrategy: 'param', paramName: 'id' } },
  { path: '/rooms/:id',           element: <RoomDetailPage />,        tab: { title: (p) => `Room ${p.id}`, icon: 'DoorOpen',     keyStrategy: 'param', paramName: 'id' } },
  { path: '/summary',             element: <MeetingSummaryPage />,    tab: { title: 'Meeting Summary', icon: 'CalendarDays', keyStrategy: 'static' } },
  { path: '/my',                  element: <MyBookingsPage />,        tab: { title: 'My Bookings',    icon: 'CalendarDays', keyStrategy: 'static' } },
```

### 8B — AppLayout.tsx nav item

- [ ] **Step 3: Add "Meeting Summary" to the Booking nav section** in `booking/src/components/layout/AppLayout.tsx`.

The current `NAV` array "Booking" section is:
```typescript
const NAV: NavSection[] = [
  {
    title: 'Booking',
    items: [
      { label: 'Rooms',       href: '/rooms', icon: DoorOpen,     permission: 'view_booking' },
      { label: 'My Bookings', href: '/my',    icon: CalendarDays, permission: 'view_booking' },
    ],
  },
```

Replace with:
```typescript
const NAV: NavSection[] = [
  {
    title: 'Booking',
    items: [
      { label: 'Rooms',            href: '/rooms',    icon: DoorOpen,     permission: 'view_booking' },
      { label: 'Meeting Summary',  href: '/summary',  icon: CalendarDays, permission: 'view_booking' },
      { label: 'My Bookings',      href: '/my',       icon: CalendarDays, permission: 'view_booking' },
    ],
  },
```

- [ ] **Step 4: Typecheck**

```
cd booking
npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | head -20
```

Expected: clean (no errors).

- [ ] **Step 5: Commit the frontend changes**

```
git add booking/src/lib/types.ts booking/src/services/api.ts booking/src/pages/MeetingSummaryPage.tsx booking/src/app/routes.tsx booking/src/components/layout/AppLayout.tsx
git commit -m "feat(booking): MeetingSummaryPage — Outlook-style day-view grid for all rooms"
```

---

## Task 9: Smoke test

**Files:** (none modified — verification only)

- [ ] **Step 1: Start uvicorn on port 8012 (avoid the running container's 8010)**

```
cd booking-api
$env:POSTGRES_HOST="localhost"; $env:POSTGRES_PORT="5432"; $env:POSTGRES_USER="epms"; $env:POSTGRES_PASSWORD="7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9"; $env:POSTGRES_DB="epms"; $env:JWT_SECRET_KEY="test-secret"; $env:SCHEDULER_ENABLED="false"
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8012 &
```

Wait ~3 seconds for startup.

- [ ] **Step 2: Mint a JWT** — get the production JWT_SECRET_KEY from the running container:

```
$SECRET = docker exec uniops_epms_api env | grep JWT_SECRET_KEY | cut -d= -f2
```

Then mint a token:
```
$TOKEN = .venv/Scripts/python.exe -c "
from jose import jwt
from datetime import datetime, timedelta
import os
secret = '$SECRET'
payload = {'sub': '00000000-0000-0000-0000-000000000001', 'role': 'requester', 'type': 'access', 'exp': datetime.utcnow() + timedelta(hours=1)}
print(jwt.encode(payload, secret, algorithm='HS256'))
"
```

If the prod secret doesn't work against the local test-secret uvicorn, use `test-secret` instead.

- [ ] **Step 3: Hit the endpoint**

```
$TODAY = Get-Date -Format 'yyyy-MM-dd'
curl -s -H "Authorization: Bearer $TOKEN" "http://localhost:8012/api/v1/bookings/day?date=$TODAY" | python -m json.tool | head -40
```

Expected: HTTP 200, JSON with `date`, `open_start`, `open_end`, `rooms` (array), `bookings` (array).

- [ ] **Step 4: Kill uvicorn and verify port freed**

```
Stop-Process -Name python -ErrorAction SilentlyContinue
```

Or if using background job, kill the job. Then verify:
```
Test-NetConnection localhost -Port 8012
```
Expected: `TcpTestSucceeded : False`

---

## Task 10: Write report

**Files:**
- Create: `.superpowers/sdd/task-19-report.md`

- [ ] **Step 1: Create `.superpowers/sdd/task-19-report.md`** with the following structure (fill in actual values from the run):

```markdown
# Task 19 Report — Meeting Summary Day View

## Status
✅ Complete

## Commit SHA
<output of `git rev-parse HEAD`>

## Pytest tail (last 10 lines)
<paste last 10 lines from the pytest run>

## Typecheck
<paste output of `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`>
(or "clean — 0 errors" if no output)

## Smoke result
<paste curl response summary — HTTP status + first few fields of JSON>

## Concerns / notes
- Route ordering: `/bookings/day` is declared before `/{booking_id}` routes in `bookings.py` — no shadowing risk.
- The `timezone` import was added to the `from datetime import ...` line in `bookings.py`.
- Overlapping bookings within a room cannot exist (DB exclusion constraint), so no stacking logic was needed in the grid.
- The current-time indicator re-renders every 60 s via `setInterval`; this is consistent with the `useDaySummary` refetch interval.
```

- [ ] **Step 2: Final commit**

```
git add .superpowers/sdd/task-19-report.md
git commit -m "feat(booking): meeting summary day view (all rooms, Outlook-style grid)"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] `GET /bookings/day?date=YYYY-MM-DD` — Task 2
- [x] `date` = local calendar day in DISPLAY_TIMEZONE, window to UTC — Task 2
- [x] `DaySummaryOut` schema — Task 1
- [x] `rooms[]`: non-disabled, sorted floor then name, maintenance included — Task 2
- [x] `bookings[]`: confirmed, overlap window, organizer_name via bulk JOIN — Task 2
- [x] Route ordering: `/day` before `/{booking_id}` — Task 2 note, `__init__.py` already has precheck before bookings, same router so ordering within router matters
- [x] TDD test file — Task 3
- [x] 2 rooms + bookings on day + next-day excluded + cancelled excluded + disabled excluded + maintenance included + local-day boundary — Task 3
- [x] Full suite must pass (≥ 264 + new) — Task 4
- [x] `bookingService.daySummary()` + `useDaySummary(refetchInterval 60_000)` — Task 6
- [x] Types in `lib/types.ts` — Task 5
- [x] `MeetingSummaryPage.tsx` at `/summary` — Task 7
- [x] Header bar: Today + arrows + date input + weekday label — Task 7
- [x] Grid: gutter, room columns min 180px, horizontal scroll, sticky room headers — Task 7
- [x] Hour + 30-min grid lines — Task 7
- [x] Booking blocks: top/height from time, clamped, teal style, title+time+organizer, title tooltip — Task 7
- [x] Current-time indicator (today only, red line, 60s recompute) — Task 7
- [x] Room header click → navigate(`/rooms/${id}`) — Task 7
- [x] No stacking (overlap impossible by DB constraint) — explicitly noted
- [x] Empty state: no rooms hint, rooms-but-no-bookings text — Task 7
- [x] Nav item "Meeting Summary" between "Rooms" and "My Bookings", permission view_booking, icon CalendarDays — Task 8
- [x] Route in `routes.tsx` — Task 8
- [x] Typecheck clean — Tasks 5, 6, 7, 8
- [x] Smoke test on port 8012, JWT mint, curl 200, kill uvicorn — Task 9
- [x] Report at `.superpowers/sdd/task-19-report.md` — Task 10
- [x] English only — enforced throughout
- [x] No chart lib (pure CSS/absolute) — Task 7
- [x] No custom dropdowns (native date input) — Task 7

**Route ordering in `__init__.py`:** The `bookings_router` is already declared after `precheck_router`. Within the router itself, `/day` GET must be declared before `/{booking_id}` PATCH/POST. In Task 2 the new route is inserted before the PATCH `/{booking_id}` block — this is correct because FastAPI processes routes in registration order within a router.

**Type consistency:**
- `DaySummaryRoom` used in `types.ts` (Task 5), imported in `MeetingSummaryPage` (Task 7) ✓
- `DaySummaryBooking` used in `types.ts` (Task 5), imported in `MeetingSummaryPage` (Task 7) ✓
- `DaySummaryOut` used in `types.ts` (Task 5), imported in `services/api.ts` (Task 6) ✓
- `useDaySummary` defined in `services/api.ts` (Task 6), imported in `MeetingSummaryPage` (Task 7) ✓
- `bookingsByRoom` is computed via `useCallback` and typed correctly as `Map<string, DaySummaryBooking[]>` ✓
