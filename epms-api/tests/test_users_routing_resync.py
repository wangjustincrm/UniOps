"""Changing a user must re-sync in-flight approvals.

Incident 2026-08-18: a Department Manager was swapped in Portal Admin. Approve
tasks for department-scoped roles are PINNED to a specific user at creation time
(approval-api engine._USER_SPECIFIC_ROLES), so 33 in-flight PA/PR tasks stayed
pointed at the previous manager — invisible to the new one, unapprovable by the
old one — until an admin remembered to run the resync script by hand. Editing the
user is what must trigger it.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.department import Department
from app.models.user import User
from app.schemas.auth import RegisterRequest

RESYNC = "app.api.v1.users.approval_client.resync_inflight"


async def _make_user(test_engine, role: str = "dept_manager") -> User:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"resync-{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass1!", full_name="Resync Target", role=role))
        await db.commit()
    return user


async def _make_department(test_engine) -> Department:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        dept = Department(code=f"RS{uuid.uuid4().hex[:4]}", name="Resync Dept")
        db.add(dept)
        await db.commit()
    return dept


@pytest.mark.asyncio
async def test_moving_a_user_to_another_department_resyncs(admin_client, test_engine):
    """Department change re-routes dept_manager approvals — the old department's
    manager must stop owning them."""
    user = await _make_user(test_engine)
    dept = await _make_department(test_engine)

    # The real /routing/resync-inflight body: `resynced`/`errors` are the
    # per-document detail LISTS, not counts (approval-api routing.py).
    engine_reply = {"resynced": [{"number": "PA-1"}, {"number": "PA-2"}], "errors": []}
    with patch(RESYNC, new=AsyncMock(return_value=engine_reply)) as spy:
        resp = await admin_client.patch(
            f"/api/v1/users/{user.id}", json={"department_id": str(dept.id)})

    assert resp.status_code == 200, resp.text
    assert spy.await_count == 1
    assert resp.json()["routing_resync"] == "ok: 2 document(s) re-synced"


@pytest.mark.asyncio
async def test_deactivating_a_user_resyncs(admin_client, test_engine):
    """A deactivated manager can no longer be resolved by the engine; their
    pinned tasks must be handed over."""
    user = await _make_user(test_engine)

    with patch(RESYNC, new=AsyncMock(return_value={"resynced": [{"number": "PR-1"}], "errors": []})) as spy:
        resp = await admin_client.delete(f"/api/v1/users/{user.id}")

    assert resp.status_code == 204, resp.text
    assert spy.await_count == 1


@pytest.mark.asyncio
async def test_renaming_a_user_does_not_resync(admin_client, test_engine):
    """Only routing-relevant fields trigger a resync — a full-name edit must not
    fire a full re-sync of every in-flight document."""
    user = await _make_user(test_engine)

    with patch(RESYNC, new=AsyncMock(return_value={"resynced": [], "errors": []})) as spy:
        resp = await admin_client.patch(
            f"/api/v1/users/{user.id}", json={"full_name": "Renamed Only"})

    assert resp.status_code == 200, resp.text
    assert spy.await_count == 0
    assert resp.json().get("routing_resync") is None


@pytest.mark.asyncio
async def test_resync_runs_after_the_change_is_committed(admin_client, test_engine):
    """approval-api reads the shared DB over its own connection, so the user edit
    must be COMMITTED before the resync is asked to re-resolve approvers —
    otherwise it re-resolves against the pre-change role and changes nothing."""
    user = await _make_user(test_engine, role="dept_manager")
    seen: dict[str, str] = {}
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def _read_role_from_another_connection(*_args, **_kwargs):
        async with factory() as db:
            seen["role"] = (await db.execute(
                select(User.role).where(User.id == user.id))).scalar_one()
        return {"resynced": [], "errors": []}

    with patch(RESYNC, new=_read_role_from_another_connection):
        resp = await admin_client.patch(
            f"/api/v1/users/{user.id}", json={"role": "requester"})

    assert resp.status_code == 200, resp.text
    assert seen["role"] == "requester", "resync ran before the role change was committed"


@pytest.mark.asyncio
async def test_a_failing_resync_still_saves_the_user(admin_client, test_engine):
    """The user edit is the primary operation; an approval-api outage must be
    reported, never roll the edit back."""
    user = await _make_user(test_engine)

    with patch(RESYNC, new=AsyncMock(side_effect=RuntimeError("approval-api down"))):
        resp = await admin_client.patch(
            f"/api/v1/users/{user.id}", json={"role": "requester"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "requester"
    assert resp.json()["routing_resync"].startswith("failed")
