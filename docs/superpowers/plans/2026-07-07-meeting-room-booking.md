# Meeting Room Booking Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the V1 meeting room booking module (booking + booking-api) per `docs/superpowers/specs/2026-07-07-meeting-room-booking-design.md` — room admin, conflict-free auto-approve booking (incl. basic recurring series), computed status display, availability suggestions, and email + Outlook Classic iMIP calendar invites over SMTP.

**Architecture:** Standalone UniOps module following the VMS pattern: `booking-api` (FastAPI, port 8010, shared Postgres, own alembic chain with separate version table) + `booking` frontend (React/Vite, port 5178). Double-booking is prevented by a Postgres `btree_gist` exclusion constraint; an application-level precheck provides friendly errors and alternative-room/time recommendations. Calendar invites are iCalendar/iTIP/iMIP (`METHOD:REQUEST`/`CANCEL`, stable UID, incrementing SEQUENCE) sent via SMTP as `multipart/alternative` + `.ics` attachment. A background asyncio loop retries failed notifications.

**Tech Stack:** FastAPI + SQLAlchemy 2 async + alembic + asyncpg; `icalendar` lib; stdlib `smtplib` in `asyncio.to_thread`; React 19 + Vite + react-router 7 + zustand + @tanstack/react-query + `@uniops/shell`.

## Global Constraints

- All user-facing UI strings are **English only** (comments may be Chinese).
- Booking-api dev port **8010**; booking frontend dev port **5178**.
- Times stored UTC (`timestamptz`); invites rendered with `TZID=America/Toronto` (setting `DISPLAY_TIMEZONE`).
- Booking rules defaults: 15-min slot granularity; duration 15 min–4 h; advance window 30 days; open hours 08:00–20:00 (all admin-configurable in `booking_config.rules`).
- Custom dropdown/combobox overlays MUST `createPortal` to `document.body` with fixed positioning.
- Full user lists must use page-through (`page_size` cap 200) — never a single default GET (silent truncation at 20).
- Mirror models MUST match physical tables column-for-column (verify via `information_schema`) — copy `vms-api/app/models/user_mirror.py`, do not invent columns.
- Frontend typecheck command: `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` (never `tsc -b`).
- Backend tests run against local docker Postgres `uniops_postgres` (override `POSTGRES_*` env; get password via `docker exec uniops_postgres env | grep POSTGRES_PASSWORD`).
- Commit after every task (branch: `feature/meeting-room-booking`). Do NOT push without user consent.
- New frontend origin (`http://localhost:5178` + prod subdomain) must be added to ALL backend `ALLOWED_ORIGINS` (8 × `*/app/core/config.py`, epms/vms/booking compose env overrides, `.env.prod`).
- Every `VITE_*` build arg used by the frontend Dockerfile MUST be declared `ARG` + `ENV` in that Dockerfile.

---

### Task 1: Scaffold booking-api service (health check green in compose)

**Files:**
- Create: `booking-api/` — copy these files verbatim from `vms-api/` then apply the listed edits:
  - `Dockerfile`, `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`
  - `app/__init__.py`, `app/main.py`, `app/core/__init__.py`, `app/core/config.py`, `app/core/security.py`, `app/core/deps.py`, `app/db/__init__.py`, `app/db/base.py`, `app/db/session.py`, `app/api/__init__.py`, `app/api/v1/__init__.py`, `app/api/v1/health.py`
  - `app/models/user_mirror.py` (verbatim copy — same table)
- Create: `booking-api/tests/__init__.py`, `booking-api/tests/conftest.py` (copy `vms-api/tests/conftest.py`, change imports `vms` → `booking` names only)
- Modify: `docker-compose.dev.yml` (add `booking-api` service)

**Interfaces:**
- Produces: `app.core.config.settings` (Settings with `API_V1_PREFIX="/api/v1"`, `DATABASE_URL`, `JWT_SECRET_KEY`, `DISPLAY_TIMEZONE`, `SCHEDULER_ENABLED`), `app.core.deps.SessionDep`, `CurrentUserPayload`, `app.db.base.Base`, `app.db.session.get_session/engine`. All later backend tasks import these.

- [ ] **Step 1: Copy vms-api skeleton**

```bash
cd /c/Project/uniops
mkdir -p booking-api/app/{core,db,api/v1,models,schemas,crud,services} booking-api/tests booking-api/alembic/versions
for f in Dockerfile requirements.txt requirements-dev.txt pyproject.toml alembic.ini; do cp vms-api/$f booking-api/$f; done
cp vms-api/alembic/env.py vms-api/alembic/script.py.mako booking-api/alembic/
cp vms-api/app/__init__.py booking-api/app/
cp vms-api/app/main.py booking-api/app/main.py
cp vms-api/app/core/{__init__.py,config.py,security.py,deps.py} booking-api/app/core/
cp vms-api/app/db/{__init__.py,base.py,session.py} booking-api/app/db/
cp vms-api/app/api/__init__.py booking-api/app/api/
cp vms-api/app/api/v1/{__init__.py,health.py} booking-api/app/api/v1/
cp vms-api/app/models/user_mirror.py booking-api/app/models/user_mirror.py
touch booking-api/app/models/__init__.py booking-api/app/schemas/__init__.py booking-api/app/crud/__init__.py booking-api/app/services/__init__.py
```

- [ ] **Step 2: Edit `booking-api/app/core/config.py`**

Apply these changes to the copied file:
- `APP_NAME: str = "Booking API"`
- Rename `REPORT_TIMEZONE` → `DISPLAY_TIMEZONE: str = "America/Toronto"` (used for iCal TZID)
- Add `"http://localhost:5178"` to `ALLOWED_ORIGINS` default list
- Keep `SCHEDULER_ENABLED: bool = True` but set `SCHEDULER_INTERVAL_SECONDS: int = 300` (5 min retry cadence)
- Delete VMS-specific settings (`APPROVAL_ENGINE_URL`); keep `EPMS_API_URL`, `FILE_SERVER_URL`, JWT settings, `POSTGRES_*`.

- [ ] **Step 3: Edit `booking-api/app/main.py`**

- Docstring → "booking-api FastAPI application factory."
- The lifespan imports `from app.services import scheduler` — Task 10 creates it; for now replace the scheduler start/stop lines with `scheduler_task = None` / remove the stop call (leave a `# Task 10 wires the notification retry scheduler here` comment) so the app boots.

- [ ] **Step 4: Edit `booking-api/app/api/v1/__init__.py`**

```python
from fastapi import APIRouter
from app.api.v1.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
```

- [ ] **Step 5: Edit `booking-api/alembic/env.py` — separate version table**

booking-api shares the `epms` database, so its alembic chain must use its own version table (same trick as budget-api). In `env.py`, in both `run_migrations_offline()` and the `context.configure(...)` inside `run_migrations_online()`, add:

```python
version_table="alembic_version_booking",
```

Also point `target_metadata` at booking models: `from app.db.base import Base` … `target_metadata = Base.metadata` (copied file already does this pattern; just confirm imports resolve).

- [ ] **Step 6: Edit `booking-api/pyproject.toml` + requirements**

- Project name → `booking-api`.
- Append to `booking-api/requirements.txt`: `icalendar>=6.0` and `openpyxl>=3.1` (xlsx room import).

- [ ] **Step 7: Add compose service**

In `docker-compose.dev.yml`, duplicate the whole `vms-api:` block (shown at lines ~314–356) as `booking-api:` right after it, with these substitutions: context `./booking-api`, container `uniops_booking_api`, ports `"8010:8010"`, volume `./booking-api:/app`, `APP_NAME: Booking API`, drop `APPROVAL_ENGINE_URL`, command port `8010`, healthcheck URL port `8010`, and add `"http://localhost:5178"` inside its `ALLOWED_ORIGINS` JSON string.

- [ ] **Step 8: Boot + verify**

```bash
docker compose -f docker-compose.dev.yml up -d booking-api
curl -s http://localhost:8010/health
```
Expected: `{"status":"ok"}` (or same shape vms-api /health returns).

- [ ] **Step 9: Commit**

```bash
git add booking-api docker-compose.dev.yml
git commit -m "feat(booking): scaffold booking-api service (port 8010, own alembic chain)"
```

---

### Task 2: Data model + initial migration (incl. no-double-booking exclusion constraint)

**Files:**
- Create: `booking-api/app/models/room.py`, `booking-api/app/models/booking.py`, `booking-api/app/models/notification.py`, `booking-api/app/models/booking_config.py`, `booking-api/app/models/audit.py`
- Modify: `booking-api/app/models/__init__.py`, `booking-api/app/db/base.py` (import models so metadata sees them — follow how vms-api registers models)
- Create: `booking-api/alembic/versions/20260707_0001_booking_initial.py`
- Test: `booking-api/tests/test_models.py`

**Interfaces:**
- Produces (later tasks import these exact names):
  - `MeetingRoom(id, name, code, campus, building, floor, area, capacity, equipment: list[str], room_type, open_time_start: time, open_time_end: time, advance_booking_days: int, status, owner_department, notes, image_file_ids: list, created_at, updated_at)` — status values `available|disabled|maintenance`
  - `Booking(id, room_id, title, description, organizer_id, attendee_ids: list, starts_at, ends_at, status, series_id, rrule, calendar_uid, ical_sequence, sync_status, created_at, updated_at)` — status `confirmed|cancelled`; sync_status `pending|sent|failed|compensating`
  - `NotificationLog(id, booking_id, notif_type, recipients: list, status, error, retry_count, sent_at, created_at)` — notif_type `created|updated|cancelled|sync_alert`; status `pending|sent|failed`
  - `BookingConfig(id, smtp_settings: dict, rules: dict, organizer_mode: str)`
  - `BookingAuditLog(id, booking_id, action, actor_id, before: dict|None, after: dict|None, created_at)`

- [ ] **Step 1: Write models**

`booking-api/app/models/room.py`:

```python
import uuid
from datetime import datetime, time

from sqlalchemy import CheckConstraint, Integer, String, Text, Time, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MeetingRoom(Base):
    __tablename__ = "meeting_rooms"
    __table_args__ = (CheckConstraint("capacity > 0", name="ck_room_capacity_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    campus: Mapped[str | None] = mapped_column(String(128))
    building: Mapped[str | None] = mapped_column(String(128))
    floor: Mapped[str | None] = mapped_column(String(64))
    area: Mapped[str | None] = mapped_column(String(128))
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    equipment: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)   # tv|projector|whiteboard|video_conf|phone_conf
    room_type: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")  # standard|training|boardroom|multi_function
    open_time_start: Mapped[time | None] = mapped_column(Time)  # None → use config default
    open_time_end: Mapped[time | None] = mapped_column(Time)
    advance_booking_days: Mapped[int | None] = mapped_column(Integer)  # None → config default
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="available")  # available|disabled|maintenance
    owner_department: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    image_file_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), onupdate=func.now())
```

`booking-api/app/models/booking.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        Index("ix_bookings_room_start", "room_id", "starts_at"),
        Index("ix_bookings_series", "series_id"),
        Index("ix_bookings_organizer", "organizer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("meeting_rooms.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    organizer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attendee_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    starts_at: Mapped[datetime] = mapped_column(nullable=False)   # timestamptz via Base convention
    ends_at: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="confirmed")  # confirmed|cancelled
    series_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rrule: Mapped[str | None] = mapped_column(Text)
    calendar_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    ical_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sync_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|sent|failed|compensating
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), onupdate=func.now())
```

NOTE: check `vms-api` `Base`/model conventions for how `datetime` maps to `TIMESTAMP WITH TIME ZONE` (vms models use `DateTime(timezone=True)` explicitly — mirror whatever `vms-api/app/models/visit.py` does; `starts_at/ends_at/created_at/updated_at` MUST be timestamptz).

`booking-api/app/models/notification.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NotificationLog(Base):
    __tablename__ = "booking_notification_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id"))
    notif_type: Mapped[str] = mapped_column(String(16), nullable=False)  # created|updated|cancelled|sync_alert
    recipients: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # email strings
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|sent|failed
    error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
```

`booking-api/app/models/booking_config.py` (single-row table, vms_config pattern):

```python
import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DEFAULT_RULES = {
    "slot_minutes": 15,
    "min_duration_minutes": 15,
    "max_duration_minutes": 240,
    "advance_days": 30,
    "default_open_start": "08:00",
    "default_open_end": "20:00",
    "notify_room_admin": False,
    "room_admin_emails": [],
}


class BookingConfig(Base):
    __tablename__ = "booking_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    smtp_settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rules: Mapped[dict] = mapped_column(JSONB, nullable=False, default=lambda: dict(DEFAULT_RULES))
    organizer_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="system")  # system|initiator
```

`booking-api/app/models/audit.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BookingAuditLog(Base):
    __tablename__ = "booking_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id"))
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # create|update|cancel|force_cancel|room_create|room_update|room_disable
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
```

Register all in `booking-api/app/db/base.py` / `models/__init__.py` following the vms-api registration pattern (models must be imported before `Base.metadata` is used by alembic/tests).

- [ ] **Step 2: Write migration**

`alembic revision` autogenerate against local docker DB, then hand-edit. The migration MUST contain, after the `bookings` table create:

```python
op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
op.execute(
    """
    ALTER TABLE bookings ADD CONSTRAINT no_double_booking
    EXCLUDE USING gist (room_id WITH =, tstzrange(starts_at, ends_at) WITH &&)
    WHERE (status = 'confirmed')
    """
)
op.execute("ALTER TABLE bookings ADD CONSTRAINT ck_booking_times CHECK (ends_at > starts_at)")
```

`downgrade()` drops the tables (not the extension). The migration must NOT touch the `users` table (mirror only).

- [ ] **Step 3: Write the failing test**

`booking-api/tests/test_models.py`:

```python
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
```

IMPORTANT: the exclusion constraint only exists via the migration SQL — `Base.metadata.create_all()` won't create it. The test conftest must run the alembic migration against the test DB (or execute the same two `op.execute` statements after `create_all`). Check how vms-api/budget-api conftest builds its test DB and follow that; budget-api uses an alembic-built `budget_test` DB — do the same with a `booking_test` DB.

- [ ] **Step 4: Run tests — expect fail (tables/migration missing), then apply migration and make them pass**

```bash
cd booking-api && POSTGRES_HOST=localhost POSTGRES_PASSWORD=<local pw> python -m pytest tests/test_models.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Apply migration to local dev DB via compose**

```bash
docker compose -f docker-compose.dev.yml exec booking-api alembic upgrade head
docker compose -f docker-compose.dev.yml exec postgres psql -U epms -d epms -c "\d bookings" | grep no_double_booking
```
Expected: exclusion constraint listed.

- [ ] **Step 6: Commit**

```bash
git add booking-api
git commit -m "feat(booking): data model + initial migration with no-double-booking exclusion constraint"
```

---

### Task 3: Permission keys (EPMS matrix) + booking-api permission dependency

**Files:**
- Modify: `epms-api/app/crud/config.py` (PERMISSION_KEYS + defaults)
- Create: `booking-api/app/core/permissions.py`
- Test: `booking-api/tests/test_permissions.py`

**Interfaces:**
- Produces: `require_perm(key: str)` FastAPI dependency → returns JWT payload dict or raises 403. Keys: `"view_booking"`, `"manage_meeting_rooms"`. All booking endpoints in Tasks 4–11 depend on it.
- `CurrentUser = Annotated[dict, Depends(require_perm("view_booking"))]`, `AdminUser = Annotated[dict, Depends(require_perm("manage_meeting_rooms"))]`

- [ ] **Step 1: Add matrix keys in epms-api**

In `epms-api/app/crud/config.py`:
- Append to `PERMISSION_KEYS` (end of the list, after the finance keys, with a comment):

```python
    # Booking module (meeting rooms). view_booking gates the whole employee-facing
    # module; manage_meeting_rooms gates room CRUD / all-bookings admin.
    "view_booking", "manage_meeting_rooms",
```

- In `_DEFAULT_ROLE_PERMISSIONS`, add `view_booking=True` for every role. Do it with a shorthand right below `_FINANCE_ALL`:

```python
_BOOKING = dict(view_booking=True)
```

and add `**_BOOKING` to every `_P(...)` call (all roles can book by default; `system_admin` already gets everything via `{k: True for k in PERMISSION_KEYS}`). `manage_meeting_rooms` stays default-False for all except system_admin — admins grant it via the Access Control Matrix UI (the matrix page renders columns from `PERMISSION_KEYS` automatically; verify no frontend change needed by loading EPMS → Admin → Access Control Matrix and seeing the two new columns).

- [ ] **Step 2: Write failing tests for booking-api permission dep**

`booking-api/tests/test_permissions.py`:

```python
import pytest

from app.core.permissions import has_permission

# has_permission(role, key, stored_matrix) — pure function under the dependency

def test_system_admin_always_allowed():
    assert has_permission("system_admin", "manage_meeting_rooms", {}) is True

def test_stored_matrix_wins():
    m = {"requester": {"view_booking": False}}
    assert has_permission("requester", "view_booking", m) is False

def test_missing_key_falls_back_to_default_view_true():
    assert has_permission("requester", "view_booking", {"requester": {}}) is True

def test_missing_key_falls_back_to_default_manage_false():
    assert has_permission("requester", "manage_meeting_rooms", {}) is False
```

- [ ] **Step 3: Implement `booking-api/app/core/permissions.py`**

```python
"""Permission checks against the shared EPMS Access Control Matrix.

booking-api reads `company_config.role_permissions` (JSONB) straight from the
shared DB — same read-only raw-SQL approach vms-api uses for SMTP config.
Stored values win; keys missing from a role's stored dict (configs predating
this module) fall back to the local defaults below. system_admin always passes.
"""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUserPayload, SessionDep

_DEFAULTS = {"view_booking": True, "manage_meeting_rooms": False}


def has_permission(role: str, key: str, stored_matrix: dict) -> bool:
    if role == "system_admin":
        return True
    role_perms = stored_matrix.get(role) or {}
    if key in role_perms:
        return bool(role_perms[key])
    return _DEFAULTS.get(key, False)


async def _load_matrix(db: AsyncSession) -> dict:
    row = (await db.execute(text("SELECT role_permissions FROM company_config LIMIT 1"))).scalar_one_or_none()
    return row if isinstance(row, dict) else {}


def require_perm(key: str):
    async def _check(payload: CurrentUserPayload, db: SessionDep) -> dict:
        matrix = await _load_matrix(db)
        if not has_permission(payload.get("role", ""), key, matrix):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return payload
    return _check


CurrentUser = Annotated[dict, Depends(require_perm("view_booking"))]
AdminUser = Annotated[dict, Depends(require_perm("manage_meeting_rooms"))]
```

- [ ] **Step 4: Run tests**

```bash
cd booking-api && python -m pytest tests/test_permissions.py -v
```
Expected: 4 passed. Also run epms-api's config tests to ensure the new keys didn't break the matrix endpoint: `cd ../epms-api && python -m pytest tests -k "permission or config" -v` (with local DB env per Global Constraints).

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/crud/config.py booking-api
git commit -m "feat(booking): view_booking + manage_meeting_rooms matrix keys and permission dependency"
```

---

### Task 4: Room admin CRUD + xlsx import + module config endpoints

**Files:**
- Create: `booking-api/app/schemas/room.py`, `booking-api/app/schemas/config.py`, `booking-api/app/crud/room.py`, `booking-api/app/crud/config.py`, `booking-api/app/api/v1/admin_rooms.py`, `booking-api/app/api/v1/admin_config.py`
- Modify: `booking-api/app/api/v1/__init__.py` (include routers)
- Test: `booking-api/tests/test_admin_rooms.py`

**Interfaces:**
- Consumes: `AdminUser` from Task 3; `MeetingRoom`, `BookingConfig`, `DEFAULT_RULES` from Task 2.
- Produces REST endpoints (all under `/api/v1`, AdminUser-gated):
  - `POST /admin/rooms` (RoomCreate → RoomOut), `PATCH /admin/rooms/{id}` (RoomUpdate → RoomOut), `POST /admin/rooms/{id}/status` (body `{"status": "available|disabled|maintenance", "notes": str|None}` → RoomOut + affected-future-bookings count), `GET /admin/rooms` (filters floor/area/status)
  - `POST /admin/rooms/import` (multipart xlsx; columns: name, code, campus, building, floor, area, capacity, equipment (comma-sep), room_type, open_time_start, open_time_end; returns `{created: n, errors: [{row, message}]}`)
  - `GET /admin/config` / `PUT /admin/config` (schema ConfigOut/ConfigUpdate: smtp_settings, rules, organizer_mode)
- Produces `app.crud.config.get_or_create_config(db) -> BookingConfig` — used by Tasks 5–10 for rules/SMTP.

Schemas (`app/schemas/room.py`) — exact shapes:

```python
class RoomBase(BaseModel):
    name: str
    code: str
    campus: str | None = None
    building: str | None = None
    floor: str | None = None
    area: str | None = None
    capacity: int = Field(gt=0)
    equipment: list[str] = []
    room_type: str = "standard"
    open_time_start: time | None = None
    open_time_end: time | None = None
    advance_booking_days: int | None = None
    owner_department: str | None = None
    notes: str | None = None
    image_file_ids: list[uuid.UUID] = []

class RoomCreate(RoomBase): ...
class RoomUpdate(BaseModel):   # all fields optional
    ...  # same fields as RoomBase but every one `| None = None`
class RoomOut(RoomBase):
    id: uuid.UUID
    status: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
```

- [ ] **Step 1: Write failing tests** — create room (201, code echoed), duplicate code → 409, capacity 0 → 422, status change to `maintenance` makes it unbookable (assert via room row status; booking-side check is Task 7's test), xlsx import happy path (build workbook in-test with openpyxl, 2 valid rows + 1 bad capacity row → `{created: 2, errors: [row 4]}`), non-admin JWT → 403.
- [ ] **Step 2: Implement crud + routers.** CRUD is thin SQLAlchemy; catch unique violation on `code` → HTTP 409. `get_or_create_config` mirrors epms `get_or_create` singleton pattern. Register routers: `api_router.include_router(admin_rooms.router, prefix="/admin/rooms", tags=["admin"])`, `api_router.include_router(admin_config.router, prefix="/admin/config", tags=["admin"])`.
- [ ] **Step 3: Run tests** — `python -m pytest tests/test_admin_rooms.py -v` → all pass.
- [ ] **Step 4: Commit** — `git commit -m "feat(booking): room admin CRUD, xlsx import, module config endpoints"`

---

### Task 5: Status computation + employee room listing/detail endpoints

**Files:**
- Create: `booking-api/app/services/availability.py`, `booking-api/app/schemas/booking.py` (BookingOut minimal), `booking-api/app/api/v1/rooms.py`
- Modify: `booking-api/app/api/v1/__init__.py`
- Test: `booking-api/tests/test_availability.py`

**Interfaces:**
- Consumes: models (Task 2), `CurrentUser` (Task 3), `get_or_create_config` (Task 4).
- Produces:
  - `compute_room_status(room: MeetingRoom, bookings_today: list[Booking], now: datetime) -> str` — returns `free|in_use|starting_soon|booked|disabled|maintenance`
  - `free_slots(room_open: tuple[time, time], busy: list[tuple[datetime, datetime]], day: date, slot_minutes: int, tz: ZoneInfo) -> list[tuple[datetime, datetime]]`
  - `GET /rooms` → `list[RoomWithStatusOut]` (RoomOut + `status_now: str`, `next_meeting_at: datetime | None`); filters: `campus, building, floor, area, min_capacity, equipment (repeatable), room_type`
  - `GET /rooms/{id}` → RoomDetailOut (RoomWithStatusOut + `today_bookings: list[BookingSlimOut]` + `week_bookings: list[BookingSlimOut]` (next 7 days)); `BookingSlimOut = {id, title, starts_at, ends_at, organizer_name}` — title/organizer visible to all staff (internal transparency; PRD shows day schedules to everyone)
  - `GET /rooms/availability?starts_at=&ends_at=&min_capacity=&equipment=&…` → `list[RoomWithStatusOut]` — same filters as `GET /rooms` plus a required time window; returns only rooms with NO confirmed overlap in the window and status=available (uses the same overlap predicate Task 6's `find_conflicts` uses)

Status rules (PRD 9.3.3): `disabled|maintenance` from `room.status`; `in_use` if `now` inside a confirmed booking; `starting_soon` if next confirmed booking starts within ≤15 min; `booked` if any future confirmed booking remains today; else `free`.

- [ ] **Step 1: Write failing tests** for `compute_room_status` (6 statuses — freeze `now`, build in-memory Booking rows, no DB needed) and `free_slots` (open 08:00–20:00, one busy 14:00–15:00 → gaps [08:00–14:00], [15:00–20:00]; slots align to 15-min grid; busy outside open hours clamps).
- [ ] **Step 2: Implement `availability.py`** — pure functions, no DB. `free_slots` walks the sorted busy list inside open hours and yields gaps ≥ slot_minutes.
- [ ] **Step 3: Implement `rooms.py` router** — one query for rooms + one grouped query for today's/week's confirmed bookings (avoid N+1: `WHERE room_id = ANY(...) AND starts_at < :week_end AND ends_at > :today_start AND status='confirmed'`), then compute statuses in Python. Organizer names resolved by joining the `users` mirror.
- [ ] **Step 4: Run tests + commit** — `git commit -m "feat(booking): computed room status + employee room list/detail endpoints"`

---### Task 6: Conflict precheck + recommendation engine

**Files:**
- Create: `booking-api/app/services/recommend.py`, `booking-api/app/api/v1/precheck.py`
- Test: `booking-api/tests/test_recommend.py`

**Interfaces:**
- Consumes: `free_slots` (Task 5), models, `CurrentUser`.
- Produces:
  - `find_conflicts(db, room_id, starts_at, ends_at, exclude_booking_ids: set | None) -> list[Booking]`
  - `suggest(db, *, room: MeetingRoom, starts_at, ends_at, attendee_count: int | None, equipment: list[str], cfg_rules: dict) -> SuggestOut` where `SuggestOut = {"nearest_slots": [{starts_at, ends_at}], "alternative_rooms": [RoomWithStatusOut]}`
  - `POST /bookings/precheck` body `{room_id, starts_at, ends_at, attendee_count?, equipment?, series?: {rrule fields, see Task 7 SeriesSpec}}` → `{"conflicts": [BookingSlimOut], "occurrence_conflicts": [{date, conflicts:[...]}], "suggestions": SuggestOut | None}` (suggestions only when conflicts exist)

Ranking (PRD 9.4.3), implemented as a sort key over candidate rooms free in the window:
1. same floor AND capacity within band (capacity ≥ needed and ≤ 2× needed) → rank 0
2. same area OR adjacent floor (float floor distance 1) → rank 1
3. rest free rooms: prefer time-satisfying, then capacity ≥ needed, then equipment superset → rank 2 with sub-keys `(capacity_delta, missing_equipment_count)`
Nearest slots: from `free_slots` for the target room on the same day, pick the 3 gaps closest to the requested start (before or after), trimmed to the requested duration.

- [ ] **Step 1: Failing tests**: conflict window detection (touching edges NOT a conflict); ranking (build 4 rooms: same-floor-same-cap, adjacent-floor, big-far, disabled → expect order + disabled excluded); nearest-slot trimming; recommendation excludes `disabled|maintenance` rooms.
- [ ] **Step 2: Implement + router.** `find_conflicts` uses `starts_at < :end AND ends_at > :start AND status='confirmed'`.
- [ ] **Step 3: Run tests + commit** — `git commit -m "feat(booking): conflict precheck + alternative room/time recommendation"`

---

### Task 7: Booking creation (single + recurring series)

**Files:**
- Create: `booking-api/app/services/recurrence.py`, `booking-api/app/crud/booking.py`, `booking-api/app/api/v1/bookings.py` (create + list-mine for now)
- Modify: `booking-api/app/api/v1/__init__.py`, `booking-api/app/schemas/booking.py`
- Test: `booking-api/tests/test_booking_create.py`, `booking-api/tests/test_recurrence.py`

**Interfaces:**
- Consumes: `find_conflicts`/`suggest` (Task 6), `CurrentUser`, config rules, audit model.
- Produces:
  - `SeriesSpec = {"freq": "daily"|"weekly", "interval": int = 1, "count": int | None, "until": date | None}` (exactly one of count/until; weekly recurs on the weekday of `starts_at`)
  - `expand_series(starts_at, ends_at, spec: SeriesSpec, *, advance_days: int, tz) -> list[tuple[datetime, datetime]]` — caps at advance window; raises `ValueError` on >60 occurrences
  - `POST /bookings` body `BookingCreate = {room_id, title, description?, attendee_ids: [uuid], starts_at, ends_at, needs_video_conf?: bool, series?: SeriesSpec}` → 201 `BookingCreatedOut = {bookings: [BookingOut], series_id: uuid|None}`; 400 with `{"detail": "conflict", "conflicts": [...], "suggestions": SuggestOut}` on any conflict; 422 on rule violations (duration/granularity/open-hours/advance-window/room disabled)
  - `GET /bookings/mine` → `list[BookingOut]` (BookingOut = BookingSlimOut + description, attendee_ids, room summary, status, series_id, rrule, sync_status)
  - `create_booking_records(db, payload, organizer, cfg) -> list[Booking]` — used again by Task 8's modify flow
  - After-commit hook: calls `notifications.enqueue(db, bookings, "created")` — Task 10 provides it; until then wire a no-op stub `app/services/notifications.py::enqueue(...)` returning None (stub created in THIS task so imports resolve).

Validation order in create: auth → payload sanity (end>start, 15-min alignment, duration within min/max) → room exists + status=available → within open hours + advance window → expand series (if any) → precheck ALL occurrences (any conflict → 400 with per-occurrence conflicts, nothing persisted) → INSERT all rows in one transaction (series shares `series_id` + `calendar_uid` + `rrule` string; single booking gets fresh `calendar_uid = f"{uuid4()}@uniops"`) → audit log row per booking → enqueue notification. Wrap flush in `try/except IntegrityError` → rollback → same 400 conflict shape (constraint is the concurrency backstop).

- [ ] **Step 1: Failing tests for `expand_series`**: weekly count=4 → 4 occurrences 7 days apart; until-date inclusive; cap at advance window; >60 → ValueError.
- [ ] **Step 2: Failing tests for POST /bookings**: happy single (201, sync_status=pending, audit row exists); conflict → 400 + suggestions + no row; series with occurrence 3 conflicting → 400 listing that date, zero rows persisted; duration 5 h → 422; room in maintenance → 422; unaligned 14:07 start → 422.
- [ ] **Step 3: Implement recurrence + crud + router.** RRULE string stored for series: `f"FREQ={freq.upper()};INTERVAL={interval}" + (f";COUNT={count}" if count else f";UNTIL={until:%Y%m%dT235959Z}")` — same string later feeds the iCal VEVENT.
- [ ] **Step 4: Run all booking tests + commit** — `git commit -m "feat(booking): auto-approve booking creation incl. recurring series expansion"`

---

### Task 8: Modify / cancel / admin bookings management

**Files:**
- Modify: `booking-api/app/api/v1/bookings.py` (PATCH, cancel), `booking-api/app/crud/booking.py`
- Create: `booking-api/app/api/v1/admin_bookings.py` (list-all + export + force cancel)
- Test: `booking-api/tests/test_booking_lifecycle.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `PATCH /bookings/{id}` body `BookingUpdate = {title?, description?, attendee_ids?, room_id?, starts_at?, ends_at?}` → BookingOut. Rules: organizer or admin only (else 403); booking already started → 403 unless admin; series member → 400 `{"detail": "series_member_immutable"}` (UI guides cancel-series-and-recreate); on any time/room change re-run conflict check excluding self; bumps `ical_sequence += 1`, sets `sync_status="pending"`, audit row with before/after, enqueues "updated".
  - `POST /bookings/{id}/cancel` query `?series=true|false` → 200 `{cancelled: n}`. Organizer or admin; series=true cancels ALL not-yet-started confirmed occurrences of the series in one transaction + ONE "cancelled" notification (one METHOD:CANCEL covers the recurring VEVENT); single cancel enqueues "cancelled". Audit `cancel` (or `force_cancel` when actor≠organizer & admin).
  - `GET /admin/bookings` (AdminUser) filters `room_id, organizer_id, date_from, date_to, status` + pagination → `{items: [BookingAdminOut], total}`; `GET /admin/bookings/export` → CSV (`text/csv`, same filters) with columns id,room_code,room_name,title,organizer,starts_at,ends_at,status,attendees_count,sync_status,created_at.

- [ ] **Step 1: Failing tests**: non-organizer PATCH → 403; admin PATCH ok; time change onto busy slot → 400 conflict; PATCH bumps sequence + sync_status pending; series member PATCH → 400; cancel single frees slot (re-book same window succeeds); cancel series cancels only future confirmed occurrences; started meeting PATCH by organizer → 403, by admin → 200; CSV export returns header row + rows.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Run tests + commit** — `git commit -m "feat(booking): booking modify/cancel with iCal sequence bump + admin management/export"`

---

### Task 9: iCalendar invite builder (iMIP payloads)

**Files:**
- Create: `booking-api/app/services/ical.py`
- Test: `booking-api/tests/test_ical.py`

**Interfaces:**
- Consumes: `Booking`, `MeetingRoom`, settings `DISPLAY_TIMEZONE`.
- Produces:
  - `build_event_ics(*, booking: Booking, room: MeetingRoom, organizer_email: str, attendee_emails: list[str], method: Literal["REQUEST","CANCEL"], rrule: str | None) -> bytes`
  - Output rules: `BEGIN:VCALENDAR` with `METHOD:{method}`, `PRODID:-//UniOps//Booking//EN`; single VEVENT; `UID` = `booking.calendar_uid`; `SEQUENCE` = `booking.ical_sequence`; `DTSTART/DTEND;TZID=America/Toronto` (converted from UTC); `SUMMARY` = title; `LOCATION` = `f"{room.name} ({', '.join(filter(None, [room.building, room.floor, room.area]))})"`; `DESCRIPTION` = description or ""; `ORGANIZER;CN=...:mailto:{organizer_email}`; one `ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{email}` per attendee; `STATUS:CANCELLED` when method=CANCEL; `RRULE:{rrule}` line when provided (series → build from the FIRST occurrence's times).

- [ ] **Step 1: Failing tests** (parse output back with `icalendar.Calendar.from_ical`): REQUEST has METHOD/UID/SEQUENCE/DTSTART with TZID/ORGANIZER/2 attendees; CANCEL has METHOD:CANCEL + STATUS:CANCELLED + same UID; series REQUEST contains RRULE FREQ=WEEKLY;COUNT=4; SEQUENCE reflects `ical_sequence=2`.
- [ ] **Step 2: Implement with the `icalendar` package** (`Calendar`, `Event`, `vCalAddress`, `vText`; `zoneinfo.ZoneInfo(settings.DISPLAY_TIMEZONE)`).
- [ ] **Step 3: Run tests + commit** — `git commit -m "feat(booking): iCalendar REQUEST/CANCEL builder (stable UID, sequence, RRULE)"`

---

### Task 10: Email notifications + retry scheduler + admin monitor

**Files:**
- Replace stub: `booking-api/app/services/notifications.py`
- Create: `booking-api/app/services/scheduler.py`, `booking-api/app/api/v1/admin_notifications.py`
- Modify: `booking-api/app/main.py` (wire scheduler start/stop in lifespan — remove Task 1's placeholder), `booking-api/app/api/v1/__init__.py`
- Test: `booking-api/tests/test_notifications.py`

**Interfaces:**
- Consumes: `build_event_ics` (Task 9), `NotificationLog`, `BookingConfig` (smtp_settings + rules.notify_room_admin/room_admin_emails + organizer_mode), user mirror (emails).
- Produces:
  - `enqueue(db, bookings: list[Booking], notif_type: str) -> NotificationLog` — resolves recipients (organizer + attendees with non-empty email + room admins if enabled; attendees WITHOUT an email are skipped and named in the log error field), inserts a `pending` NotificationLog, then attempts immediate send.
  - `send_notification(db, log: NotificationLog) -> bool` — loads SMTP config (module JSONB → fallback shared `company_config` SMTP, copy `vms-api/app/services/notifications.py::_load_smtp_config` verbatim with table name `booking_config`); builds email: `Subject: [Booking] {Created|Updated|Cancelled}: {title} — {room} {local time}`; body `multipart/alternative` (plain-text summary part + `text/calendar; method=REQUEST|CANCEL; charset=utf-8` part) **plus** `.ics` attachment (`application/ics`, filename `invite.ics`); From = smtp from_email; ORGANIZER per `organizer_mode` (`system` → from_email, `initiator` → organizer's email). On success: log status=sent + booking.sync_status=sent; on exception: status=failed, error text, booking.sync_status=failed. SMTP unconfigured → log-only mode: log the intended email at INFO, mark log status=sent with error="smtp_not_configured (logged only)" (graceful degrade, VMS pattern).
  - `scheduler.start(app)` / `scheduler.stop(task)` — asyncio loop every `SCHEDULER_INTERVAL_SECONDS`, Postgres advisory lock (copy vms-api scheduler skeleton), each tick: pick `NotificationLog WHERE status='failed' AND retry_count < 5`, set booking sync_status=compensating, retry with backoff (skip logs whose `retry_count`-th retry window hasn't elapsed: next_try = sent_at/created_at + 5min * 2^retry_count); on 5th failure insert a `sync_alert` NotificationLog to room_admin_emails.
  - `GET /admin/notifications` (AdminUser) filters status/type + pagination → `{items: [NotificationLogOut], total}`; `POST /admin/notifications/{id}/resend` → re-runs send_notification.

- [ ] **Step 1: Failing tests** (monkeypatch `smtplib.SMTP` with a recorder): created enqueue sends 1 email to organizer+2 attendees, message contains both `text/calendar` part with `METHOD:REQUEST` and `.ics` attachment; attendee without email skipped + noted; SMTP raising → log failed + booking sync_status failed; resend endpoint flips to sent; smtp-unconfigured logs-only and still marks sent; cancel notif carries METHOD:CANCEL.
- [ ] **Step 2: Implement notifications + scheduler; wire lifespan.** Hook `enqueue` calls into Task 7 create (type created), Task 8 update (updated) and cancel (cancelled) — replace the stub import sites; series create/cancel → ONE notification for the series (RRULE VEVENT), passing the series' first-occurrence booking plus rrule.
- [ ] **Step 3: Run the full backend suite** — `python -m pytest tests -v` → all pass.
- [ ] **Step 4: Commit** — `git commit -m "feat(booking): iMIP email notifications, retry scheduler, admin sync monitor"`

---

### Task 11: Directory endpoint (attendee picker source)

**Files:**
- Create: `booking-api/app/api/v1/directory.py`
- Modify: `booking-api/app/api/v1/__init__.py`
- Test: `booking-api/tests/test_directory.py`

**Interfaces:**
- Produces: `GET /users/directory?q=<search>` (CurrentUser) → `list[{id, full_name, email}]` — queries the local `users` mirror `WHERE is_active AND (full_name ILIKE %q% OR email ILIKE %q%) ORDER BY full_name LIMIT 50`; **excludes users with empty email** (can't receive invites); q optional (empty → first 50). Frontend picker searches server-side so the 20-row pagination trap never applies.

- [ ] **Step 1: Failing test**: seeds 3 users (one inactive, one no-email), q matches by name and by email, inactive + no-email excluded.
- [ ] **Step 2: Implement + include router. Run test.**
- [ ] **Step 3: Commit** — `git commit -m "feat(booking): attendee directory search endpoint"`

---

### Task 12: Scaffold booking frontend (boots in compose, session handoff + branding work)

**Files:**
- Create: `booking/` — copy from `vms/` then edit: `package.json`, `vite.config.ts`, `tsconfig*.json`, `index.html`, `eslint.config.js`, `Dockerfile` (if vms has one; prod Dockerfile otherwise mirrors finance frontend's), `src/main.tsx`, `src/index.css`, `src/App.tsx`, `src/lib/api.ts`, `src/lib/signOut.ts`, `src/lib/utils.ts`, `src/hooks/useBranding.ts`, `src/store/*` (auth store), `src/components/layout/*` (AppLayout + Sidebar), `src/components/StatusBadge.tsx`
- Modify: `docker-compose.dev.yml` (add `booking-frontend` + `booking_node_modules` volume)

**Interfaces:**
- Produces: `api` client (booking-api, BASE from `VITE_API_URL` default `http://localhost:8010`), `epmsApi` client, auth store key `booking-auth` (fallback `portal-auth`), `useBranding('booking')`, `useRolePermissions()` hook (fetch EPMS `GET /api/v1/config/role-permissions`, returns `{matrix, role, loading}`), `AppLayout` with permission-gated nav + landing-page selection on `/`.

- [ ] **Step 1: Copy vms frontend, rename** — package name `booking`, localStorage keys `vms-auth`→`booking-auth`, tab-persist storageKey if present → `uniops:booking:tabs:v1`, port refs 5176→5178, api port 8008→8010. `useBranding.ts`: module key `'booking'`. Delete VMS pages/components (visit/visitor/badge/health/QR files) — keep layout, StatusBadge, store, lib.
- [ ] **Step 2: Nav config inside AppLayout** — items: `Rooms` (`/rooms`, perm view_booking), `My Bookings` (`/my`, perm view_booking), `Admin` section: `Rooms Admin` (`/admin/rooms`), `All Bookings` (`/admin/bookings`), `Notifications` (`/admin/notifications`), `Settings` (`/admin/settings`) — all four perm `manage_meeting_rooms`. Landing on `/`: wait for matrix load → navigate(first visible item, {replace}) → none visible → "No access. Back to Portal." (copy the finance AppLayout landing pattern per module conventions).
- [ ] **Step 3: Routes in App.tsx** — react-router 7, single BrowserRouter; stub pages (`<div>Rooms</div>` etc.) for now; Tasks 13–16 fill them.
- [ ] **Step 4: Compose service** — duplicate `vms-frontend` block as `booking-frontend`: container `uniops_booking_frontend`, port `5178:5178`, volumes `./booking:/app` + `booking_node_modules:/app/node_modules` + shell mount; env `VITE_API_URL: http://localhost:8010`, `VITE_EPMS_API_URL: http://localhost:8000`, `VITE_PORTAL_URL: http://localhost:5174`; command port 5178. Add `booking_node_modules:` to the compose `volumes:` section.
- [ ] **Step 5: Verify boot** — `docker compose -f docker-compose.dev.yml up -d booking-frontend`, open `http://localhost:5178` → shows layout (branding falls back to defaults until CORS task 17; that's expected — note it, don't chase it). Typecheck: `cd booking && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` → clean.
- [ ] **Step 6: Commit** — `git commit -m "feat(booking): scaffold booking frontend shell (port 5178, permission-gated nav)"`

---

### Task 13: Rooms list page (search + filters + status polling)

**Files:**
- Create: `booking/src/pages/RoomsPage.tsx`, `booking/src/services/api.ts` (typed service layer: `roomService`, `bookingService`, `directoryService`, `adminService` — mirror vms `src/services/api.ts` shape), `booking/src/lib/types.ts` (TS mirrors of RoomWithStatusOut, BookingOut, SuggestOut, SeriesSpec…)
- Modify: `booking/src/App.tsx`

**Interfaces:**
- Consumes: `GET /rooms`, `GET /rooms/availability` via react-query with `refetchInterval: 60_000`.
- Produces: filter bar (date + start/end time, attendees count, campus/building/floor/area selects, equipment multi-check, room type) — when a time window is set, page calls `/rooms/availability` and shows only free rooms; without time it lists all with live status. Room cards: name, code, location line, capacity, equipment icons (lucide), StatusBadge mapping `free→green, in_use→red, starting_soon→amber, booked→blue, disabled/maintenance→gray`, next meeting time, "Book" button → navigates to `/rooms/{id}?start=…&end=…`. All copy English.

- [ ] Steps: build types + service layer → page → wire route → typecheck clean → manual smoke against dev API (create a room via curl/admin, see it listed, status flips when a booking spans now) → commit `feat(booking): rooms list page with availability filters + 60s status polling`.

---

### Task 14: Room detail + booking form (precheck, suggestions, recurrence, attendees)

**Files:**
- Create: `booking/src/pages/RoomDetailPage.tsx`, `booking/src/pages/BookingCreatePage.tsx`, `booking/src/components/DayTimeline.tsx`, `booking/src/components/AttendeePicker.tsx`, `booking/src/components/RecurrencePicker.tsx`, `booking/src/components/SuggestionPanel.tsx`

**Interfaces:**
- Consumes: `GET /rooms/{id}`, `POST /bookings/precheck`, `POST /bookings`, `GET /users/directory?q=`.
- Produces:
  - `DayTimeline` — horizontal 15-min grid across the room's open hours; busy blocks labeled with title; a 7-day mini-strip below (per-day busy density). Pure CSS/flex, no chart lib.
  - `AttendeePicker` — debounced (300 ms) directory search combobox, multi-select chips; **overlay rendered via `createPortal` to `document.body` with fixed positioning**, outside-click closes excluding trigger+overlay refs (per Global Constraints).
  - `RecurrencePicker` — None | Daily | Weekly; interval; end after N occurrences or by date. Emits `SeriesSpec | null`.
  - `BookingCreatePage` — title, description, room (locked when arriving from a room page), start/end (15-min steps), attendees, needs_video_conf checkbox, recurrence. On any time/room/series change → debounce → `POST /bookings/precheck` → inline result: green "Available" or `SuggestionPanel` listing nearest free slots (click → adopts times) and alternative rooms (click → swaps room). Submit → success toast + navigate `/my`; 400 conflict → render same SuggestionPanel from response.

- [ ] Steps: components → pages → routes → typecheck → manual smoke (book, conflict path shows suggestions, recurring weekly ×4 books 4 rows) → commit `feat(booking): room detail timeline + booking form with live precheck and suggestions`.

---

### Task 15: My Bookings page (edit / cancel)

**Files:**
- Create: `booking/src/pages/MyBookingsPage.tsx`, `booking/src/pages/BookingEditPage.tsx`

**Interfaces:**
- Consumes: `GET /bookings/mine`, `PATCH /bookings/{id}`, `POST /bookings/{id}/cancel?series=`.
- Produces: upcoming/past tabs; row: title, room, time, attendees count, status + sync_status badge, series icon when series_id. Actions: Edit (disabled for series members with tooltip "Recurring meetings can't be edited individually — cancel the series and re-book."), Cancel (confirm dialog; for series → radio "This occurrence cannot be cancelled alone in V1 — cancel entire series?" mapping to `?series=true`). Edit page = create form prefilled (via same components), room switch allowed, precheck excludes self.

- [ ] Steps: pages → routes → typecheck → manual smoke (edit time regenerates invite: sync_status back to pending then sent) → commit `feat(booking): my bookings list with edit/cancel flows`.

---

### Task 16: Admin pages (rooms, all bookings, notifications monitor, settings)

**Files:**
- Create: `booking/src/pages/admin/RoomsAdminPage.tsx`, `booking/src/pages/admin/RoomFormModal.tsx`, `booking/src/pages/admin/RoomImportModal.tsx`, `booking/src/pages/admin/AllBookingsPage.tsx`, `booking/src/pages/admin/NotificationsPage.tsx`, `booking/src/pages/admin/SettingsPage.tsx`

**Interfaces:**
- Consumes: Task 4/8/10 admin endpoints; file-api for room images (upload via existing file-api client pattern — copy `vms/src/services` attachment upload usage; store returned file ids in `image_file_ids`).
- Produces: RoomsAdmin table (filter floor/area/status; create/edit modal incl. equipment checkboxes, open hours, status select w/ notes; disabling a room with future bookings shows the affected count returned by the status endpoint and links to AllBookings filtered to that room); Import modal (download template link explaining columns, upload xlsx, per-row error list); AllBookings (filters + force-cancel with reason confirm + Export CSV button hitting the export endpoint); Notifications (status/type filter, error text, Resend button); Settings (rules form: slot/min/max/advance/open hours/notify-room-admin toggle + admin emails chips; SMTP form: host/port/user/password/TLS/from + organizer_mode select `System mailbox|Meeting organizer`).

- [ ] Steps: pages → routes → typecheck → manual smoke (import 2 rooms via xlsx; resend a failed notification) → commit `feat(booking): admin pages — rooms CRUD/import, all bookings, sync monitor, settings`.

---

### Task 17: Portal + CORS + branding integration

**Files:**
- Modify: `portal/src/components/layout/navConfig.tsx`, `portal/src/pages/PortalHome.tsx`, portal AdminPanel file containing `MODULE_TAGLINE_KEYS`
- Modify (CORS defaults): `epms-api/app/core/config.py`, `approval-api`, `mdm-api`, `identity-api`, `finance-api`, `file-api`, `expense-api`, `budget-api`, `vms-api` — each `app/core/config.py` `ALLOWED_ORIGINS` gains `"http://localhost:5178"`
- Modify: `docker-compose.dev.yml` — every service that overrides `ALLOWED_ORIGINS` via env (epms-api, vms-api, booking-api, any others found by grep) gets `http://localhost:5178` appended to the JSON string

**Interfaces:**
- Consumes: `view_booking`/`manage_meeting_rooms` matrix keys (Task 3); portal conventions (`isNavItemVisible`, `anyPermission`).

- [ ] **Step 1: navConfig** — add to MODULES:

```tsx
export const BOOKING_ACCESS_PERMS = ['view_booking', 'manage_meeting_rooms']
{
  key: 'booking',
  label: 'Booking',
  icon: <CalendarClock className="h-4 w-4" />,   // lucide-react
  href: `${BOOKING_URL}`,            // module ROOT + #__session handoff, follow how VMS_URL item builds it
  anyPermission: BOOKING_ACCESS_PERMS,
}
```

with `const BOOKING_URL = import.meta.env.VITE_BOOKING_URL || 'http://localhost:5178'` — mirror exactly how VMS/Finance URLs are declared in this file.

- [ ] **Step 2: PortalHome card** — import `BOOKING_ACCESS_PERMS`, compute `hasBookingAccess = BOOKING_ACCESS_PERMS.some(p => matrix?.[role]?.[p])` (match the existing hasFinanceAccess implementation exactly), conditionally append card `{title: 'Meeting Rooms', description: 'Find and book meeting rooms', href: bookingRootWithSession}`.
- [ ] **Step 3: Taglines** — add `{ key: 'booking', label: 'Booking' }` to `MODULE_TAGLINE_KEYS`.
- [ ] **Step 4: CORS sweep** — `grep -rn "5177" --include=config.py` to find every backend default list; add 5178 line beside it in all 9 backends (8 existing + booking-api already has it); same grep in `docker-compose.dev.yml` for env overrides. Add `VITE_BOOKING_URL` to portal-frontend compose env (`http://localhost:5178`).
- [ ] **Step 5: Verify** — restart portal + epms-api + booking stack; log in to Portal as system_admin → Booking card + sidebar item appear; click through → booking module loads with company logo/name + booking tagline (branding fetch no longer CORS-blocked); a requester-role user sees the card; a role with view_booking toggled off in the Matrix does NOT see it.
- [ ] **Step 6: Commit** — `git commit -m "feat(booking): portal entry, matrix-gated visibility, tagline key, CORS for 5178"`

---

### Task 18: Production wiring + full verification

**Files:**
- Modify: `docker-compose.prod.yml` (booking-api + booking-frontend services, mirror the vms pair incl. image `ghcr.io/...uniops-booking-api:${TAG}` naming), `Caddyfile` (booking subdomain block, copy the vms block), `migrate-prod.sh` (add booking-api migration step if the script enumerates services), `.env.prod`/`/tmp/uniops-domain.env` documentation note (VITE_BOOKING_URL + new prod origins in ALLOWED_ORIGINS)
- Create: `booking/Dockerfile` prod stage if not already (mirror finance frontend Dockerfile; declare **ARG + ENV for every VITE_\*†**: VITE_API_URL, VITE_EPMS_API_URL, VITE_PORTAL_URL; portal Dockerfile gains ARG/ENV `VITE_BOOKING_URL`)

- [ ] **Step 1: prod compose + Caddyfile + Dockerfiles** as above; prod `ALLOWED_ORIGINS` for all backends gain `https://booking.<domain>`.
- [ ] **Step 2: Full backend suite** — `cd booking-api && python -m pytest tests -v` (local docker DB env) → ALL pass; `cd epms-api && python -m pytest tests -v` → no regressions.
- [ ] **Step 3: Frontend typechecks** — booking + portal: `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` → clean.
- [ ] **Step 4: End-to-end dev smoke (success path is mandatory)** — as admin: create 2 rooms; as employee: search by time+capacity → book with 2 attendees → NotificationLog row sent (log-only mode ok in dev) → modify time → sequence=1 + updated notif → cancel → METHOD:CANCEL notif; recurring weekly ×3 → 3 rows + single RRULE notif; conflict attempt → suggestions panel shows alternates. Record results in the final report.
- [ ] **Step 5: Real-mail verification note** — actual Outlook Classic rendering of `text/calendar` can only be validated against the company SMTP gateway; leave a checklist item in the PR/report for the user to run one real invite test pre-release (send to self, accept, verify calendar event; then modify + cancel and confirm Outlook updates). This is a release gate, not a dev gate.
- [ ] **Step 6: Commit** — `git commit -m "feat(booking): production compose/Caddy wiring + e2e smoke"`

---

## Deferred (explicitly OUT of this plan — matches spec §8)

Stats dashboard (utilization/top rooms), per-occurrence series edit + exception dates, admin bulk ops, Outlook add-in, mobile, RSVP status write-back, IoT/door signage. `alembic upgrade` on prod, image push, and `.env` TAG bump happen at release time per `reference_uniops_prod_release_workflow` — NOT during this plan.
