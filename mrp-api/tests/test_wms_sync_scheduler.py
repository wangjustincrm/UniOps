"""The WMS mirror refreshes itself, on a schedule an admin controls.

Before this, `wms_inventory_lots` only moved when somebody remembered to call
POST /admin/wms-sync — and on the day the Inventory feature shipped, nobody
did. These tests pin the three decisions that make "somebody remembered" stop
being part of the design: when a sync is due, what the interval parameter
accepts, and what one tick does in each state.

The asyncio loop itself is never started here (the `client` fixture drives the
app through ASGITransport, which does not run lifespan) — `run_tick_with_session`
is the seam, exactly as booking-api's scheduler tests use `_run_tick_with_session`.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.api.v1.params import WMS_SYNC_INTERVAL_KEY, set_param
from app.models.sync_state import MrpSyncState
from app.services.wms_sync import scheduler

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


# ── is_due: the schedule is a property of the data, not of this process ──────

def test_a_mirror_that_has_never_synced_is_due_immediately():
    assert scheduler.is_due(last_attempt_at=None, interval_minutes=5, now=NOW) is True


def test_not_due_until_the_interval_has_elapsed():
    assert scheduler.is_due(
        last_attempt_at=NOW - timedelta(minutes=4, seconds=59),
        interval_minutes=5, now=NOW) is False
    assert scheduler.is_due(
        last_attempt_at=NOW - timedelta(minutes=5),
        interval_minutes=5, now=NOW) is True


def test_interval_zero_means_never_automatically():
    """0 is the off switch, including for a mirror that has never synced —
    otherwise turning automatic sync off would still fire one last run."""
    assert scheduler.is_due(last_attempt_at=None, interval_minutes=0, now=NOW) is False
    assert scheduler.is_due(
        last_attempt_at=NOW - timedelta(days=30), interval_minutes=0, now=NOW) is False


def test_a_last_attempt_in_the_future_is_not_treated_as_overdue():
    """Clock skew between the app and the database must not produce a sync on
    every single tick until they agree."""
    assert scheduler.is_due(
        last_attempt_at=NOW + timedelta(minutes=10), interval_minutes=5, now=NOW) is False


def test_a_naive_timestamp_is_read_as_utc():
    """The column is timestamptz, but a driver returning a naive datetime must
    not make `now - last` raise and take the scheduler down."""
    assert scheduler.is_due(
        last_attempt_at=(NOW - timedelta(hours=1)).replace(tzinfo=None),
        interval_minutes=5, now=NOW) is True


# ── resolve_interval_minutes: a junk parameter row cannot stall the loop ─────

@pytest.mark.parametrize("raw,expected", [
    (5, 5),
    (0, 0),
    (30.0, 30),
    (None, 5),        # no row yet → the default
    ("15", 5),        # a string is not a number → default, not a crash
    (True, 5),        # bool would otherwise read as "every 1 minute"
    (-3, 0),          # negative → off, never "overdue forever"
])
def test_resolve_interval_minutes(raw, expected):
    assert scheduler.resolve_interval_minutes(raw) == expected


# ── one tick, in each state ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_tick_does_nothing_when_the_interval_is_zero(db_session, monkeypatch):
    called = []
    monkeypatch.setattr(scheduler, "run_wms_sync", lambda db: called.append(db))
    monkeypatch.setattr(scheduler, "wms_configured", lambda: True)
    await set_param(db_session, WMS_SYNC_INTERVAL_KEY, 0, None)

    assert await scheduler.run_tick_with_session(db_session) == "disabled"
    assert called == []


@pytest.mark.anyio
async def test_tick_is_quiet_when_wms_is_not_configured(db_session, monkeypatch):
    """WMS_* blank is a documented deployment state ("feature hidden"), not a
    fault — the loop must not spend every tick raising."""
    monkeypatch.setattr(scheduler, "wms_configured", lambda: False)
    await set_param(db_session, WMS_SYNC_INTERVAL_KEY, 5, None)

    assert await scheduler.run_tick_with_session(db_session) == "not_configured"


@pytest.mark.anyio
async def test_tick_syncs_a_mirror_that_has_never_synced(db_session, monkeypatch):
    ran = []

    async def _fake_sync(db):
        ran.append(db)
        return {"lots": 3}

    monkeypatch.setattr(scheduler, "wms_configured", lambda: True)
    monkeypatch.setattr(scheduler, "run_wms_sync", _fake_sync)
    await set_param(db_session, WMS_SYNC_INTERVAL_KEY, 5, None)

    assert await scheduler.run_tick_with_session(db_session) == "synced"
    assert len(ran) == 1


@pytest.mark.anyio
async def test_tick_waits_out_the_interval(db_session, monkeypatch):
    ran = []

    async def _fake_sync(db):
        ran.append(db)
        return {"lots": 3}

    monkeypatch.setattr(scheduler, "wms_configured", lambda: True)
    monkeypatch.setattr(scheduler, "run_wms_sync", _fake_sync)
    await set_param(db_session, WMS_SYNC_INTERVAL_KEY, 60, None)
    now = datetime.now(timezone.utc)
    db_session.add(MrpSyncState(source="wms", status="success", row_count=1,
                                last_synced_at=now, last_success_at=now,
                                updated_at=now))
    await db_session.commit()

    assert await scheduler.run_tick_with_session(db_session) == "not_due"
    assert ran == []


@pytest.mark.anyio
async def test_a_failing_sync_does_not_end_the_scheduler(db_session, monkeypatch):
    """run_wms_sync records its own last_error and re-raises; the tick has to
    absorb that, or one bad WMS response stops every future sync in this
    process."""
    async def _boom(db):
        raise RuntimeError("ORA-12541: TNS:no listener")

    monkeypatch.setattr(scheduler, "wms_configured", lambda: True)
    monkeypatch.setattr(scheduler, "run_wms_sync", _boom)
    await set_param(db_session, WMS_SYNC_INTERVAL_KEY, 5, None)

    assert await scheduler.run_tick_with_session(db_session) == "failed"


@pytest.mark.anyio
async def test_a_failed_attempt_still_delays_the_next_one(db_session, monkeypatch):
    """Retrying a down WMS once per interval is right; retrying it every tick
    is not. Due-ness is measured from the last ATTEMPT for exactly this."""
    from app.services.wms_sync import service

    def _boom():
        raise RuntimeError("ORA-12541: TNS:no listener")

    monkeypatch.setattr(service, "fetch_inventory", _boom)
    monkeypatch.setattr(scheduler, "wms_configured", lambda: True)
    await set_param(db_session, WMS_SYNC_INTERVAL_KEY, 60, None)

    assert await scheduler.run_tick_with_session(db_session) == "failed"
    state = await db_session.get(MrpSyncState, "wms")
    assert state.status == "failed"
    assert state.last_synced_at is not None
    # Nothing was replaced, so nothing on screen got any fresher.
    assert state.last_success_at is None

    assert await scheduler.run_tick_with_session(db_session) == "not_due"
