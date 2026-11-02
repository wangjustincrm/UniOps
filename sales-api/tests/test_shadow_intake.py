"""The shadow run at intake (§5.4): files stay where Zoho reads them, every
stored document is flagged, and only intake problems raise tasks."""
import pytest_asyncio
import sqlalchemy as sa

from app.platform.actors import SYSTEM_ACTOR
from app.services.settings import SETTINGS, SHADOW_MODE
from tests.test_intake import PO_BROKEN, x12
from tests.test_intake import env as p2_env  # noqa: F401 — a fixture


@pytest_asyncio.fixture
async def env(p2_env):  # noqa: F811 — the P2 fixture, under the name these tests use
    return p2_env


async def shadow(env, on=True):
    async with env.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, on)
        await u.commit()


async def test_after_read_is_leave_whatever_the_connection_says_and_documents_are_flagged(env):
    c = await env.add_conn(after_read="MOVE")
    await shadow(env)
    env.fake.files["/in/a.997"] = x12("997")
    env.fake.files["/in/po-note.json"] = PO_BROKEN
    r = await env.intake.run_pass(c)
    assert r.stored == 2 and env.fake.count("move") == 0 and env.fake.count("delete") == 0
    assert {"/in/a.997", "/in/po-note.json"} <= set(env.fake.files)  # still where Zoho reads them
    assert [d["shadow"] for d in await env.q("SELECT shadow FROM sales_documents")] == [True, True]
    log = [m["message"] for m in await env.q("SELECT message FROM sales_log WHERE message LIKE '%shadow run%' ORDER BY id")]
    assert len(log) == 2 and all("after-read MOVE not applied" in m for m in log)
    # an intake problem is still a task: it would be a real one after the cutover too
    assert [t["type"] for t in await env.q("SELECT type FROM tasks")] == ["sales_unreadable_file"]


async def test_without_the_shadow_run_the_policy_applies(env):  # the positive control
    c = await env.add_conn(after_read="MOVE")
    env.fake.files["/in/a.997"] = x12("997")
    await env.intake.run_pass(c)
    assert env.fake.count("move") == 1
    assert [d["shadow"] for d in await env.q("SELECT shadow FROM sales_documents")] == [False]


async def test_a_file_that_cannot_be_read_is_not_moved_to_the_error_folder(env, monkeypatch):
    c = await env.add_conn()
    await shadow(env)
    env.fake.files["/in/x.945"] = x12("945")

    async def broken(conn, path):  # noqa: ARG001
        raise OSError("connection reset")
    monkeypatch.setattr(env.fake, "get", broken)
    r = await env.intake.run_pass(c)
    assert r.failed == 1 and env.fake.count("move") == 0 and "/in/x.945" in env.fake.files
    assert [t["type"] for t in await env.q("SELECT type FROM tasks")] == ["sales_file_not_stored"]


async def test_a_bad_stored_value_reads_as_on(env):
    async with env.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.text(
            "INSERT INTO sales_settings (key, value, created_by_name) VALUES ('shadow_mode', '\"maybe\"', 'test')"))
        await u.commit()
    SETTINGS.invalidate()
    async with env.factory(SYSTEM_ACTOR) as u:
        assert await SETTINGS.get(u.session, SHADOW_MODE) is True  # the restrictive side
