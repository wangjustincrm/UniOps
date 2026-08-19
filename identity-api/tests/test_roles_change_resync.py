"""Changing someone's PRIMARY role must re-sync in-flight approvals.

Incident 2026-08-18: Access Control moved Engineering's Department Manager to
`requester` and promoted a colleague. Approve tasks for department-scoped roles
are PINNED to a specific person at creation time (approval-api engine's
_USER_SPECIFIC_ROLES — broadcasting them would leak documents across
departments), so 25 PA + 8 PR tasks stayed pinned to the outgoing manager: the
incoming one saw nothing, the outgoing one could no longer approve, and the only
cure was an admin remembering to run the resync script.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.main import app
from scripts.seed_authz import seed_authz

pytestmark = pytest.mark.asyncio
BASE = "/identity/v1"
RESYNC = "app.api.v1.authz.approval_client.resync_inflight"


@pytest.fixture
async def seeded(db_session):
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS role_permissions jsonb DEFAULT '{}'::jsonb"))
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS custom_roles jsonb DEFAULT '[]'::jsonb"))
    await seed_authz(db_session)
    await db_session.commit()
    yield


def _admin():
    token = create_access_token(str(uuid.uuid4()), "system_admin")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _dept_manager(test_engine):
    from tests.conftest import make_user
    return await make_user(test_engine, role="dept_manager")


async def test_demoting_a_department_manager_resyncs_inflight_approvals(seeded, test_engine):
    """The incident, in one call: dept_manager → requester must hand that
    person's pinned approve tasks to whoever now holds the post."""
    u = await _dept_manager(test_engine)

    # The real /routing/resync-inflight body: `resynced`/`errors` are the
    # per-document detail LISTS, not counts (approval-api routing.py).
    engine_reply = {"resynced": [{"number": "PA-1"}, {"number": "PR-2"}], "errors": []}
    with patch(RESYNC, new=AsyncMock(return_value=engine_reply)) as spy:
        async with _admin() as c:
            r = await c.put(f"{BASE}/authz/users/{u.id}/roles",
                            json={"primary": "requester", "additional": []})

    assert r.status_code == 200, r.text
    assert spy.await_count == 1
    assert r.json()["routing_resync"] == "ok: 2 document(s) re-synced"


async def test_granting_an_additional_role_does_not_resync(seeded, test_engine):
    """Additional roles live in `user_roles`; the engine pins by PRIMARY role
    only, so granting one cannot strand a task — don't re-sync every in-flight
    document for it."""
    u = await _dept_manager(test_engine)

    with patch(RESYNC, new=AsyncMock(return_value={"resynced": [], "errors": []})) as spy:
        async with _admin() as c:
            r = await c.put(f"{BASE}/authz/users/{u.id}/roles",
                            json={"primary": "dept_manager", "additional": ["finance_bp"]})

    assert r.status_code == 200, r.text
    assert spy.await_count == 0
    assert r.json()["routing_resync"] is None


async def test_resync_runs_after_the_role_change_is_committed(seeded, test_engine):
    """approval-api re-resolves approvers over its OWN connection to the shared
    DB, so the new role has to be committed before it is asked — otherwise it
    reads the old role and leaves every task where it was."""
    u = await _dept_manager(test_engine)
    seen: dict[str, str] = {}
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def _read_role_from_another_connection(*_args, **_kwargs):
        async with factory() as db:
            seen["role"] = (await db.execute(sa.text(
                "SELECT role FROM users WHERE id = :i"), {"i": str(u.id)})).scalar_one()
        return {"resynced": [], "errors": []}

    with patch(RESYNC, new=_read_role_from_another_connection):
        async with _admin() as c:
            r = await c.put(f"{BASE}/authz/users/{u.id}/roles",
                            json={"primary": "requester", "additional": []})

    assert r.status_code == 200, r.text
    assert seen["role"] == "requester", "resync ran before the role change was committed"


async def test_a_failing_resync_still_saves_the_role_change(seeded, test_engine):
    """The role change is the primary operation — an approval-api outage is
    reported, never rolled back onto the admin."""
    u = await _dept_manager(test_engine)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    with patch(RESYNC, new=AsyncMock(side_effect=RuntimeError("approval-api down"))):
        async with _admin() as c:
            r = await c.put(f"{BASE}/authz/users/{u.id}/roles",
                            json={"primary": "requester", "additional": []})

    assert r.status_code == 200, r.text
    assert r.json()["routing_resync"].startswith("failed")
    async with factory() as db:
        role = (await db.execute(sa.text(
            "SELECT role FROM users WHERE id = :i"), {"i": str(u.id)})).scalar_one()
    assert role == "requester"
