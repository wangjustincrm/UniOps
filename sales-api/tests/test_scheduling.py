"""Per-connection scheduling (user decision 2026-11-02): when a connection's
job is due (interval, pause, active window, Toronto time), single flight per
connection, Run now, the system job on its own, what a run writes on its
connection, the schedule's edit and audit, the runs' scope, the scheduler's
settings and the Health job list."""
import asyncio
from datetime import datetime, time, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.domain.schedule import Window, describe, is_due, next_run, parse_days
from app.models.config import Connection
from app.platform.actors import SYSTEM_ACTOR
from app.services.settings import SCHEDULER_PAUSED, SETTINGS
import pytest_asyncio

from tests.test_intake import env, make_scheduler, x12  # noqa: F401 — env is a fixture
from tests.test_operations_api import C, NOBODY, OP, READER, RETRY, make_conn
from tests.test_operations_api import api as ops_api  # noqa: F401 — a fixture


@pytest_asyncio.fixture
async def e(env):  # noqa: F811 — the intake fixture (fake SFTP), under a name of its own
    return env


@pytest_asyncio.fixture
async def http(ops_api):  # noqa: F811 — the operations API fixture
    return ops_api

UTC = timezone.utc


def at(s: str) -> datetime:  # a UTC instant
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


# ── the rules ─────────────────────────────────────────────────────────────────
def test_due_needs_its_time_no_pause_and_the_window():
    now = at("2026-11-04 15:00")  # Wednesday 10:00 in Toronto
    every = Window()
    assert is_due(now, next_run_at=None, paused=False, window=every)  # never ran: now
    assert is_due(now, next_run_at=now, paused=False, window=every)
    assert not is_due(now, next_run_at=now + timedelta(seconds=1), paused=False, window=every)
    assert not is_due(now, next_run_at=None, paused=True, window=every)
    office = Window(time(6), time(22), parse_days("Mon,Tue,Wed,Thu,Fri"))
    assert is_due(now, next_run_at=None, paused=False, window=office)
    assert not is_due(at("2026-11-04 03:30"), next_run_at=None, paused=False, window=office)  # 22:30 Tue
    assert not is_due(at("2026-11-07 15:00"), next_run_at=None, paused=False, window=office)  # Saturday
    assert next_run(now, 5) == now + timedelta(minutes=5)


def test_a_window_across_midnight_belongs_to_the_day_it_starts():
    night = Window(time(22), time(2), ("Fri",))
    assert night.contains(at("2026-11-07 03:30"))      # Fri 22:30 Toronto (EST: UTC-5)
    assert night.contains(at("2026-11-07 06:30"))      # Sat 01:30 — Friday's window
    assert not night.contains(at("2026-11-07 07:30"))  # Sat 02:30
    assert not night.contains(at("2026-11-08 03:30"))  # Sat 22:30 — Saturday is not listed
    assert not night.contains(at("2026-11-06 06:30"))  # Fri 01:30 — Thursday's window


def test_the_window_is_toronto_time_across_daylight_saving():
    morning = Window(time(6), time(22))
    assert not morning.contains(at("2026-03-07 10:30"))  # 05:30 EST before the change
    assert morning.contains(at("2026-03-09 10:30"))      # 06:30 EDT after it (same UTC time)
    assert morning.contains(at("2026-11-02 11:30"))      # 06:30 EST after the fall change


def test_days_are_read_in_week_order_and_unknown_days_refused():
    assert parse_days("fri, mon ,Wed") == ("Mon", "Wed", "Fri") and parse_days("") == ()
    with pytest.raises(ValueError):
        parse_days("Mon,Funday")
    assert describe(5, Window(time(6), time(22), parse_days("Mon,Tue,Wed,Thu,Fri"))) == "every 5 min, Mon–Fri 06:00–22:00"
    assert describe(10, Window()) == "every 10 min"


# ── the jobs over the database ────────────────────────────────────────────────
async def conn_row(t, cid):
    [r] = await t.q("SELECT last_run_at, last_run_status, last_run_summary, last_error, next_run_at, version "
                      "FROM sales_connections WHERE id = :i", i=cid)
    return r


async def set_conn(t, cid, **values):
    async with t.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(Connection.__table__).where(Connection.__table__.c.id == cid).values(**values))
        await u.commit()


async def test_a_connection_runs_when_due_and_writes_its_last_and_next_run(e):
    c = await e.add_conn()
    e.fake.files["/in/a.945"] = x12("945")
    audit_before = await e.q("SELECT id FROM sales_audit WHERE entity = 'sales_connections'")
    sched = make_scheduler(e)
    [conn_run, system_run] = await sched.tick()  # never ran: due now; the system job too
    assert (conn_run.scope, system_run.scope) == (f"connection:{c.id}", "system")
    row = await conn_row(e, c.id)
    assert (row["last_run_status"], row["last_run_summary"], row["last_error"]) == ("SUCCEEDED", "1 files, 0 errors", None)
    assert row["next_run_at"] == conn_run.started_at + timedelta(minutes=5)
    assert row["version"] == 1  # bookkeeping: no new version (an open edit form stays valid) …
    assert await e.q("SELECT id FROM sales_audit WHERE entity = 'sales_connections'") == audit_before  # … nor an audit row
    assert [r.scope for r in await sched.tick()] == []  # neither due again yet


async def test_not_due_paused_master_paused_or_outside_its_window_it_does_not_run(e):
    c = await e.add_conn()
    sched = make_scheduler(e)
    job = sched.connection(c.id)
    await set_conn(e, c.id, next_run_at=datetime.now(UTC) + timedelta(minutes=3))
    assert await job.tick() is None
    await set_conn(e, c.id, next_run_at=None, schedule_paused=True)
    assert await job.tick() is None
    await set_conn(e, c.id, schedule_paused=False, active_from=time(0, 0), active_to=time(0, 1))  # a minute a day
    assert await job.tick() is None
    await set_conn(e, c.id, active_from=None, active_to=None)
    async with e.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SCHEDULER_PAUSED, True)
        await u.commit()
    assert await job.tick() is None and await sched.system.tick() is None  # the master switch stops every job
    assert await job.tick(trigger="MANUAL", force=True) is not None       # Run now ignores the schedule


async def test_single_flight_is_per_connection(e):
    a = await e.add_conn(name="a", folder="/a")
    b = await e.add_conn(name="b", folder="/b")
    sched = make_scheduler(e)
    e.fake.gate = asyncio.Event()
    first = asyncio.create_task(sched.connection(a.id).tick(trigger="MANUAL", force=True))
    for _ in range(100):
        if await e.q("SELECT 1 FROM sales_runs WHERE status = 'RUNNING'"):
            break
        await asyncio.sleep(0.02)
    assert await sched.connection(a.id).claim() is None    # the same connection: refused
    other = await sched.connection(b.id).claim()           # another one may run
    assert other is not None
    e.fake.gate.set()
    assert (await first).status == "SUCCEEDED"
    assert (await sched.connection(b.id).execute(other)).status == "SUCCEEDED"


async def test_a_failed_run_is_written_on_its_connection_and_the_system_job_still_runs(e, monkeypatch):
    c = await e.add_conn()

    async def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(e.intake, "run_pass", boom)
    sched = make_scheduler(e)
    runs = await sched.tick()
    assert [(r.scope, r.status) for r in runs] == [(f"connection:{c.id}", "FAILED"), ("system", "SUCCEEDED")]
    row = await conn_row(e, c.id)
    assert row["last_run_status"] == "FAILED" and row["last_error"] == "RuntimeError: disk full"
    assert row["last_run_summary"] == "RuntimeError: disk full" and row["next_run_at"] is not None


# ── over HTTP ─────────────────────────────────────────────────────────────────
async def wait_runs(api):
    await asyncio.gather(*list(api.runtime.background._tasks))


async def test_the_schedule_is_edited_with_sales_config_and_audited(http):
    c = await make_conn(http)
    assert (c["schedule_minutes"], c["schedule_paused"], c["active_days"]) == (5, False, None)  # SFTP: 5
    body = {"schedule_minutes": 15, "active_from": "06:00:00", "active_to": "22:00:00", "active_days": "fri,mon"}
    assert (await http.put(f"{C}/{c['id']}?version=1", json=body, headers=READER)).status_code == 403
    r = await http.put(f"{C}/{c['id']}?version=1", json=body, headers=OP)
    assert r.status_code == 200, r.text
    assert (r.json()["schedule_minutes"], r.json()["active_days"]) == (15, "Mon,Fri")
    trail = (await http.get(f"{C}/{c['id']}/audit", headers=READER)).json()
    assert trail[0]["after"]["schedule_minutes"] == 15 and trail[0]["before"]["schedule_minutes"] == 5
    one_end = await http.put(f"{C}/{c['id']}?version=2", json={"active_to": None}, headers=OP)
    assert one_end.status_code == 422 and one_end.json()["error"]["code"] == "connection.active_window"
    assert (await http.put(f"{C}/{c['id']}?version=2", json={"schedule_minutes": 0}, headers=OP)).status_code == 422
    assert (await http.put(f"{C}/{c['id']}?version=2", json={"active_days": "Mon,Funday"}, headers=OP)).status_code == 422
    from app.services.connections import ConnectionService
    built = ConnectionService(None).build({"name": "store", "kind": "SHOPIFY", "after_read": "LEAVE", "mode": "TEST"})
    assert built.schedule_minutes == 10  # a new Shopify connection: 10
    csv = (await http.get(f"{C}/export.csv", headers=READER)).text.splitlines()[0]
    assert "schedule_minutes,schedule_paused,active_from,active_to,active_days" in csv


async def test_run_now_per_connection_ignores_the_schedule_not_its_single_flight(http):
    c = await make_conn(http, host_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOYyzHbjnpUgsd3f+LSfKEJkQUehyBzwkRpZ0hBihPDh")
    await http.put(f"{C}/{c['id']}?version=1", json={"schedule_paused": True}, headers=OP)
    assert (await http.post(f"{C}/{c['id']}/run-now", headers=READER)).status_code == 403
    http.fake.gate = asyncio.Event()
    r = await http.post(f"{C}/{c['id']}/run-now", headers=RETRY)
    assert r.status_code == 202
    again = await http.post(f"{C}/{c['id']}/run-now", headers=RETRY)
    assert again.status_code == 409 and again.json()["error"]["code"] == "run.already_running"
    http.fake.gate.set()
    await wait_runs(http)
    run = (await http.get(f"/api/v1/runs/{r.json()['run_id']}", headers=READER)).json()
    assert (run["scope"], run["trigger"], run["status"]) == (f"connection:{c['id']}", "MANUAL", "SUCCEEDED")
    row = (await http.get(f"{C}/{c['id']}", headers=READER)).json()
    assert row["last_run_status"] == "SUCCEEDED" and row["next_run_at"]
    # the Runs list by scope, and the system job on its own
    s = await http.post("/api/v1/runs/run-now/system", headers=RETRY)
    assert s.status_code == 202
    await wait_runs(http)
    mine = (await http.get(f"/api/v1/runs?filter=scope:eq:connection:{c['id']}", headers=READER)).json()
    assert [x["id"] for x in mine["items"]] == [r.json()["run_id"]]
    meta = (await http.get("/api/v1/runs/meta", headers=READER)).json()
    assert {"system", "connections"} <= {v["key"] for v in meta["views"]}
    paused = (await http.get("/api/v1/config/connections?filter=needs_attention:eq:true", headers=READER)).json()
    assert [x["name"] for x in paused["items"]] == ["radiant"]  # the "Paused or failing" view


async def test_scheduler_settings_and_the_health_job_list(http):
    assert (await http.get("/api/v1/config/scheduler", headers=READER)).json() == {"paused": False, "system_minutes": 1}
    assert (await http.put("/api/v1/config/scheduler", json={"paused": True, "system_minutes": 2}, headers=READER)).status_code == 403
    r = await http.put("/api/v1/config/scheduler", json={"paused": True, "system_minutes": 2}, headers=OP)
    assert r.status_code == 200 and r.json() == {"paused": True, "system_minutes": 2}
    assert (await http.put("/api/v1/config/scheduler", json={"paused": False, "system_minutes": 0}, headers=OP)).status_code == 422
    c = await make_conn(http)
    await http.put(f"{C}/{c['id']}?version=1", json={"active_from": "06:00:00", "active_to": "22:00:00",
                                                    "active_days": "Mon,Tue,Wed,Thu,Fri"}, headers=OP)
    h = (await http.get("/api/v1/operations/health", headers=READER)).json()
    jobs = {j["scope"]: j for j in h["jobs"]}
    assert jobs["system"]["interval_minutes"] == 2 and jobs["system"]["paused"] is True
    assert jobs[f"connection:{c['id']}"]["schedule"] == "every 5 min, Mon–Fri 06:00–22:00"
    assert jobs[f"connection:{c['id']}"]["paused"] is True  # the master switch is on
    assert (h["scheduler_paused"], h["system_minutes"]) == (True, 2)
    audit = await http.get("/api/v1/config/scheduler", headers=NOBODY)
    assert audit.status_code == 403


async def test_a_job_overdue_by_more_than_three_intervals_is_flagged(http):
    c = await make_conn(http)
    async with http.runtime.uow_factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(Connection.__table__).where(Connection.__table__.c.id == c["id"]).values(
            next_run_at=datetime.now(UTC) - timedelta(minutes=16)))  # 5-minute schedule: more than 3 intervals late
        await u.commit()
    from app.core.config import settings
    settings_on = settings.model_copy(update={"SALES_SCHEDULER_ENABLED": True})
    from app.services.health import Health
    async with http.runtime.uow_factory(SYSTEM_ACTOR) as u:
        h = await Health(u, settings_on).checks()
    [job] = [j for j in h.jobs if j.connection_id == c["id"]]
    assert job.overdue is True
    assert next(x for x in h.checks if x.key == "scheduler").result == "WARNING"
