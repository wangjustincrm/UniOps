# Task 2 Report: Data model + initial migration

## Status

DONE

## Commit

`1c3bbf0` — `feat(booking): data model + initial migration with no-double-booking exclusion constraint`

## Test Summary

```
3 passed in 0.99s
```

`tests/test_models.py::test_overlapping_confirmed_bookings_rejected PASSED`
`tests/test_models.py::test_cancelled_booking_frees_slot PASSED`
`tests/test_models.py::test_back_to_back_ok PASSED`

## What Was Done

### TDD Cycle

Tests written first (`tests/test_models.py`) and confirmed failing with `ModuleNotFoundError: No module named 'app.models.booking'` before any model code was created.

### Models Created

All five model files created verbatim per the brief, with one deliberate delta: every `datetime` column that the brief specified as plain `Mapped[datetime]` was given an explicit `DateTime(timezone=True)` type argument to match the `vms-api/app/models/visit.py` pattern (`planned_arrival`, `actual_arrival`, etc. all use `DateTime(timezone=True)`). This ensures `TIMESTAMP WITH TIME ZONE` in the DB, not `TIMESTAMP` without timezone.

- `app/models/room.py` — `MeetingRoom`
- `app/models/booking.py` — `Booking`
- `app/models/notification.py` — `NotificationLog`
- `app/models/booking_config.py` — `BookingConfig`
- `app/models/audit.py` — `BookingAuditLog`
- `app/models/__init__.py` — imports all models in FK-dependency order so `Base.metadata` is populated before alembic/create_all runs

### Migration

`alembic/versions/20260707_0001_booking_initial.py` (revision `20260707_0001`):
- Creates `meeting_rooms`, `bookings`, `booking_notification_log`, `booking_audit_log`, `booking_config`
- After creating `bookings`: installs `btree_gist` extension, then adds `no_double_booking` EXCLUDE constraint and `ck_booking_times` CHECK constraint
- Does NOT touch `users` table
- `downgrade()` drops all booking tables but intentionally leaves the `btree_gist` extension (it is DB-wide and may be used by other services)
- Applied to shared dev DB (`epms` on localhost:5432) — verified with `\d bookings` showing the `no_double_booking` EXCLUDE constraint

### Migration applied to dev DB

```
INFO  [alembic.runtime.migration] Running upgrade  -> 20260707_0001, booking initial migration
```

`\d bookings` confirmed:
```
"no_double_booking" EXCLUDE USING gist (room_id WITH =, tstzrange(starts_at, ends_at) WITH &&) WHERE (status::text = 'confirmed'::text)
```

### Conftest Changes

`tests/conftest.py` updated:
1. Added `import sqlalchemy` for `sqlalchemy.text()` usage
2. Extended `test_engine` fixture to execute — after `Base.metadata.create_all()` — the `btree_gist` extension and the two ALTER TABLE statements (one at a time; asyncpg rejects multi-statement `execute()` calls). This gives tests the same exclusion constraint semantics that production gets from the migration.
3. Added `db_session` per-test fixture that yields an `AsyncSession` inside a transaction that is always rolled back on teardown (tables stay clean between tests).

### Test DB Strategy

Used `Base.metadata.create_all()` + manual DDL execution in conftest (not `alembic upgrade head`) — simpler and avoids maintaining a separate alembic state in the test DB. The `booking_test` database was created with `psql CREATE DATABASE booking_test`.

## Concerns

None. All deliverables completed cleanly.

---

## Fix: test-session savepoint isolation + booking_config.rules server_default

### Finding 1 — db_session fixture savepoint isolation (conftest.py)

**Problem:** The old fixture did `async with session.begin(): yield session; await session.rollback()`. The `rollback()` after the `begin()` context manager exits is a no-op (the context manager has already committed or rolled back). More critically, an `IntegrityError` raised inside a test aborts the shared connection's transaction; any subsequent test using the same connection would hit `InFailedSqlTransaction`.

**Fix:** Rewrote `db_session` to the standard savepoint-isolation pattern:
1. `conn = await engine.connect()` — dedicated connection per test
2. `trans = await conn.begin()` — outer real transaction
3. `AsyncSession(bind=conn, join_transaction_mode="create_savepoint", ...)` — session's `begin()` issues SAVEPOINTs, not real BEGINs
4. `finally: await session.close(); await trans.rollback(); await conn.close()` — unconditional full rollback

This means an `IntegrityError` only aborts to the savepoint; the outer connection stays alive. The next test gets a fresh connection and is never poisoned.

### Finding 2 — booking_config.rules server_default (migration)

**Problem:** `server_default="{}"` in the migration meant raw-SQL/seed inserts would get an empty `{}` for `rules`, while the SQLAlchemy model default is the full `DEFAULT_RULES` dict. These were inconsistent.

**Fix:** Updated the migration `server_default` to the full JSON literal matching `DEFAULT_RULES`:
```json
{"slot_minutes": 15, "min_duration_minutes": 15, "max_duration_minutes": 240, "advance_days": 30, "default_open_start": "08:00", "default_open_end": "20:00", "notify_room_admin": false, "room_admin_emails": []}
```

Also ran `ALTER TABLE booking_config ALTER COLUMN rules SET DEFAULT '...'::jsonb` against the live dev DB (`uniops_postgres / epms`) so the DB column default now matches the migration file.

### Verification

Added `test_session_isolated_after_integrity_error` — runs after the `IntegrityError` test, does a trivial `flush()`, and asserts `room.id is not None`. This would have raised `InFailedSqlTransaction` with the old fixture.

```
4 passed in 0.92s
```

```
tests/test_models.py::test_overlapping_confirmed_bookings_rejected PASSED
tests/test_models.py::test_cancelled_booking_frees_slot PASSED
tests/test_models.py::test_back_to_back_ok PASSED
tests/test_models.py::test_session_isolated_after_integrity_error PASSED
```
