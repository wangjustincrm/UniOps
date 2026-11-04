"""Automatic runs as a switch in the app (user 2026-11-04). SALES_SCHEDULER_ENABLED
says this server may run them; `scheduler.paused` is the user's switch, Off until
someone turns it on, so a database that never set it (a fresh one, a copy of
another server's) starts nothing even with the environment on."""
from datetime import UTC, datetime, timedelta

import pytest_asyncio
import sqlalchemy as sa

from app.core.config import settings
from app.models.config import Connection
from app.platform.actors import SYSTEM_ACTOR
from app.services.health import Health
from app.services.settings import SCHEDULER_PAUSED, SETTINGS
from tests.test_intake import automatic_runs_on, env, make_scheduler, x12  # noqa: F401 — env is a fixture
from tests.test_operations_api import C, OP, READER, make_conn
from tests.test_operations_api import api as ops_api  # noqa: F401 — a fixture

S = "/api/v1/config/scheduler"
ENV_ON = settings.model_copy(update={"SALES_SCHEDULER_ENABLED": True})


@pytest_asyncio.fixture
async def http(ops_api):  # noqa: F811 — the operations API fixture
    return ops_api


async def runs(t):
    return await t.q("SELECT scope, trigger FROM sales_runs ORDER BY id")


async def test_never_set_means_off_a_real_tick_runs_nothing(env):  # noqa: F811
    await env.add_conn()
    env.fake.files["/in/a.945"] = x12("945")
    assert await env.q("SELECT 1 FROM sales_settings WHERE key = 'scheduler.paused'") == []  # never set
    assert not await make_scheduler(env).tick()
    assert await runs(env) == [] and env.fake.calls == []  # no job ran, the server was not touched


async def test_a_bad_stored_value_reads_as_off(env):  # noqa: F811
    await env.add_conn()
    async with env.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SCHEDULER_PAUSED, False)  # a row to spoil
        await u.commit()
        await u.session.execute(sa.text("UPDATE sales_settings SET value = '\"banana\"' WHERE key = 'scheduler.paused'"))
        await u.commit()
    SETTINGS.invalidate()
    assert not await make_scheduler(env).tick()
    assert await runs(env) == []


async def test_on_a_due_connection_runs_off_nothing_runs_but_run_now_still_does(env):  # noqa: F811
    c = await env.add_conn()
    env.fake.files["/in/a.945"] = x12("945")
    sched = make_scheduler(env)
    await automatic_runs_on(env.factory)
    ran = await sched.tick()
    assert {r.scope for r in ran} == {f"connection:{c.id}", "system"}
    async with env.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SCHEDULER_PAUSED, True)
        await u.session.execute(sa.update(Connection.__table__).values(next_run_at=None))  # due again
        await u.commit()
    before = len(await runs(env))
    assert not await sched.tick()
    assert len(await runs(env)) == before
    assert await sched.connection(c.id).tick(trigger="MANUAL", force=True) is not None  # Run now is the person's, not the schedule's


async def test_the_switch_needs_sales_config_is_audited_and_says_which_way(http):
    assert (await http.get(S, headers=READER)).json()["paused"] is True
    assert (await http.put(S, json={"paused": False}, headers=READER)).status_code == 403
    assert (await http.get(S, headers=READER)).json()["paused"] is True  # the refusal changed nothing
    r = await http.put(S, json={"paused": False}, headers=OP)
    assert r.status_code == 200 and r.json()["paused"] is False
    assert (await http.put(S, json={"paused": True}, headers=OP)).status_code == 200
    async with http.runtime.uow_factory(SYSTEM_ACTOR) as u:
        rows = (await u.session.execute(sa.text(
            "SELECT reason, user_name FROM sales_audit WHERE entity = 'sales_settings' ORDER BY id"))).mappings().all()
    assert [r["reason"] for r in rows][-2:] == ["automatic runs on", "automatic runs off"]


async def test_saving_the_minutes_never_touches_the_switch(http):
    await http.put(S, json={"paused": True}, headers=OP)
    r = await http.put(S, json={"system_minutes": 7}, headers=OP)
    assert r.status_code == 200 and (r.json()["paused"], r.json()["system_minutes"]) == (True, 7)
    await http.put(S, json={"paused": False}, headers=OP)
    assert (await http.get(S, headers=READER)).json()["system_minutes"] == 7


async def test_health_off_is_a_choice_not_an_alarm_however_long(http):
    c = await make_conn(http)
    async with http.runtime.uow_factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SCHEDULER_PAUSED, True)
        await u.session.execute(sa.update(Connection.__table__).where(Connection.__table__.c.id == c["id"]).values(
            next_run_at=datetime.now(UTC) - timedelta(days=3)))
        await u.commit()
        await u.session.execute(sa.text("UPDATE sales_settings SET updated_at = now() - interval '3 days', "
                                        "created_at = now() - interval '3 days' WHERE key = 'scheduler.paused'"))
        await u.commit()
    async with http.runtime.uow_factory(SYSTEM_ACTOR) as u:
        h = await Health(u, ENV_ON).checks()
    row = next(x for x in h.checks if x.key == "scheduler")
    assert row.result == "PASSED" and row.detail.startswith("automatic runs are off")
    assert not any(x.key.startswith("overdue:") for x in h.checks)
    assert not any(j.overdue for j in h.jobs)


async def test_health_the_three_states_and_overdue_only_while_on(http):
    c = await make_conn(http)
    async with http.runtime.uow_factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(Connection.__table__).where(Connection.__table__.c.id == c["id"]).values(
            next_run_at=datetime.now(UTC) - timedelta(minutes=30)))
        await u.commit()
    await automatic_runs_on(http.runtime.uow_factory)
    env_off = settings.model_copy(update={"SALES_SCHEDULER_ENABLED": False})
    async with http.runtime.uow_factory(SYSTEM_ACTOR) as u:
        on, unavailable = await Health(u, ENV_ON).checks(), await Health(u, env_off).checks()
    assert next(x for x in on.checks if x.key == "scheduler").result == "WARNING"
    assert any(j.overdue for j in on.jobs)
    row = next(x for x in unavailable.checks if x.key == "scheduler")
    assert row.result == "WARNING" and "not available on this server" in row.detail  # not hidden by the switch
    assert not any(j.overdue for j in unavailable.jobs)  # stored On, but this server runs nothing


async def test_the_list_says_overdue_only_while_on(http, monkeypatch):
    from types import SimpleNamespace
    from app.services.connections import ConnectionService
    c = await make_conn(http)
    async with http.runtime.uow_factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(Connection.__table__).values(next_run_at=datetime.now(UTC) - timedelta(minutes=30)))
        await u.commit()
    monkeypatch.setattr(ConnectionService, "settings", SimpleNamespace(SALES_SCHEDULER_ENABLED=True))
    assert not any(x["overdue"] for x in (await http.get(C, headers=READER)).json()["items"])  # Off: never set
    await automatic_runs_on(http.runtime.uow_factory)
    assert next(x for x in (await http.get(C, headers=READER)).json()["items"] if x["id"] == c["id"])["overdue"] is True
