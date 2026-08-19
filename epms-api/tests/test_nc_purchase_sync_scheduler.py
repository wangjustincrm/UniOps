"""The NC purchase mirror runs itself, on an interval an admin controls.

Before this, the only trigger was the button in Portal -> Admin, so how current
UniOps' purchase orders and goods receipts were depended on who last pressed
it. These tests pin the three decisions that replace "somebody remembered":
when a run is due, what the interval accepts, and what one tick does in each
state.

The asyncio loop is never started here — `run_tick()` is the seam, the same way
the mrp-api WMS scheduler tests use `run_tick_with_session`.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.nc_purchase_sync import service as svc
from app.tasks import nc_purchase_sync_scheduler as sched

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


# ── is_due ──────────────────────────────────────────────────────────────────

def test_a_mirror_that_has_never_synced_is_due():
    assert sched.is_due(last_started_at=None, interval_minutes=60, now=NOW) is True


def test_not_due_until_the_interval_has_elapsed():
    assert sched.is_due(last_started_at=NOW - timedelta(minutes=59),
                        interval_minutes=60, now=NOW) is False
    assert sched.is_due(last_started_at=NOW - timedelta(minutes=60),
                        interval_minutes=60, now=NOW) is True


def test_zero_is_the_off_switch():
    assert sched.is_due(last_started_at=None, interval_minutes=0, now=NOW) is False
    assert sched.is_due(last_started_at=NOW - timedelta(days=7),
                        interval_minutes=0, now=NOW) is False


def test_clock_skew_does_not_make_it_permanently_overdue():
    assert sched.is_due(last_started_at=NOW + timedelta(minutes=30),
                        interval_minutes=60, now=NOW) is False


def test_a_naive_timestamp_is_read_as_utc():
    assert sched.is_due(last_started_at=(NOW - timedelta(hours=2)).replace(tzinfo=None),
                        interval_minutes=60, now=NOW) is True


# ── resolve_interval_minutes ────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, 60),      # nobody has chosen → the default
    (0, 0),
    (30, 30),
    (-5, 0),         # negative → off, never "overdue forever"
    (99999, 1440),   # clamped: a hand-edited row cannot ask for a run every second
    (True, 60),      # bool would otherwise read as "every 1 minute"
    ("30", 60),      # a string is not an integer → default, not a crash
])
def test_resolve_interval_minutes(raw, expected):
    assert sched.resolve_interval_minutes(raw) == expected


# ── one tick, in each state ─────────────────────────────────────────────────

def _interval(monkeypatch, minutes: int) -> None:
    async def _load():
        return minutes
    monkeypatch.setattr(sched, "load_interval_minutes", _load)


def _last_run(monkeypatch, started_at):
    async def _last():
        return started_at
    monkeypatch.setattr(sched, "last_run_started_at", _last)


@pytest.mark.asyncio
async def test_tick_does_nothing_when_the_schedule_is_off(monkeypatch):
    _interval(monkeypatch, 0)
    monkeypatch.setattr(svc, "nc_configured", lambda: True)
    calls = []
    monkeypatch.setattr(svc, "start_run", lambda *a, **k: calls.append(a))

    assert await sched.run_tick() == "disabled"
    assert calls == []


@pytest.mark.asyncio
async def test_tick_is_quiet_when_nc_is_not_configured(monkeypatch):
    _interval(monkeypatch, 60)
    monkeypatch.setattr(svc, "nc_configured", lambda: False)

    assert await sched.run_tick() == "not_configured"


@pytest.mark.asyncio
async def test_tick_waits_out_the_interval(monkeypatch):
    _interval(monkeypatch, 60)
    monkeypatch.setattr(svc, "nc_configured", lambda: True)
    _last_run(monkeypatch, datetime.now(timezone.utc) - timedelta(minutes=5))
    calls = []
    monkeypatch.setattr(svc, "start_run", lambda *a, **k: calls.append(a))

    assert await sched.run_tick() == "not_due"
    assert calls == []


@pytest.mark.asyncio
async def test_tick_runs_an_incremental_sync_when_due(monkeypatch):
    """Never `full`: that mode deletes and rebuilds the mirror, which is why the
    API makes a human type a confirmation phrase for it."""
    _interval(monkeypatch, 60)
    monkeypatch.setattr(svc, "nc_configured", lambda: True)
    _last_run(monkeypatch, None)
    calls = []

    def _start(mode, started_by, **kwargs):
        calls.append((mode, started_by, kwargs))
        return "run-id"

    monkeypatch.setattr(svc, "start_run", _start)

    assert await sched.run_tick() == "synced"
    assert len(calls) == 1
    assert calls[0][0] == "incremental"
    # Nobody pressed anything — attributing the run to a person would be a lie.
    assert calls[0][1] is None


@pytest.mark.asyncio
async def test_a_run_already_in_flight_is_not_an_error(monkeypatch):
    """An admin pressing the button, or another replica winning the race, is
    the single-flight gate working — not something to log as a failure."""
    _interval(monkeypatch, 60)
    monkeypatch.setattr(svc, "nc_configured", lambda: True)
    _last_run(monkeypatch, None)

    def _busy(*a, **k):
        raise svc.SyncAlreadyRunning("an NC purchase sync is already running")

    monkeypatch.setattr(svc, "start_run", _busy)

    assert await sched.run_tick() == "already_running"


@pytest.mark.asyncio
async def test_a_failing_sync_does_not_end_the_scheduler(monkeypatch):
    _interval(monkeypatch, 60)
    monkeypatch.setattr(svc, "nc_configured", lambda: True)
    _last_run(monkeypatch, None)

    def _boom(*a, **k):
        raise RuntimeError("ORA-12541: TNS:no listener")

    monkeypatch.setattr(svc, "start_run", _boom)

    assert await sched.run_tick() == "failed"


# ── the interval endpoint ───────────────────────────────────────────────────
#
# PATCH /interval writes to the singleton company_config row, which production
# always has and a freshly created test schema does not — so seed it the way
# the app itself would, rather than teaching the endpoint to invent one.

@pytest.fixture
async def company_config(test_engine):
    from app.crud.config import get_or_create
    from app.db import session as session_module

    async with session_module.AsyncSessionLocal() as db:
        cfg = await get_or_create(db)
        cfg.nc_purchase_sync_interval_minutes = None
        await db.commit()



@pytest.mark.asyncio
async def test_interval_round_trips_and_shows_up_in_status(admin_client, company_config):
    r = await admin_client.patch("/api/v1/admin/nc-purchase-sync/interval",
                                 json={"minutes": 120})
    assert r.status_code == 200
    assert r.json()["interval_minutes"] == 120

    status = (await admin_client.get("/api/v1/admin/nc-purchase-sync/status")).json()
    assert status["interval_minutes"] == 120


@pytest.mark.asyncio
async def test_zero_is_accepted_and_reports_no_next_run(admin_client, company_config):
    r = await admin_client.patch("/api/v1/admin/nc-purchase-sync/interval",
                                 json={"minutes": 0})
    assert r.status_code == 200
    status = (await admin_client.get("/api/v1/admin/nc-purchase-sync/status")).json()
    assert status["interval_minutes"] == 0
    assert status["next_due_at"] is None


@pytest.mark.parametrize("bad", [-1, 1441])
@pytest.mark.asyncio
async def test_out_of_range_intervals_are_rejected(admin_client, company_config, bad):
    r = await admin_client.patch("/api/v1/admin/nc-purchase-sync/interval",
                                 json={"minutes": bad})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_setting_the_interval_is_system_admin_only(requester_client):
    r = await requester_client.patch("/api/v1/admin/nc-purchase-sync/interval",
                                     json={"minutes": 30})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_status_defaults_the_interval_when_nobody_has_chosen(admin_client, company_config):
    """A fresh company_config row has NULL, which must read as the default
    rather than as 0 — 0 means somebody deliberately switched it off."""
    status = (await admin_client.get("/api/v1/admin/nc-purchase-sync/status")).json()
    assert status["interval_minutes"] == sched.DEFAULT_INTERVAL_MINUTES
