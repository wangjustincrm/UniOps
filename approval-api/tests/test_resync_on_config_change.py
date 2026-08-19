"""Routing config changes must re-sync in-flight approvals automatically.

Incident 2026-08-18: Engineering's Department Manager was swapped. dept_manager
approve tasks are PINNED to a specific user at creation time (by design — see
engine._USER_SPECIFIC_ROLES), so 33 in-flight PA/PR tasks stayed pointed at the
previous manager: the new manager saw nothing in their Task Inbox and the old one
could no longer approve. The fix was a MANUAL resync script run; nothing made the
config change trigger it. These tests pin the automatic trigger.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt
from sqlalchemy import select

from app.core.config import settings
from app.crud.engine import execute_action
from app.db.base import get_db
from app.main import app
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User

pytestmark = pytest.mark.asyncio
BASE = "/approval/v1"


def _client(role="system_admin"):
    token = jose_jwt.encode({"sub": str(uuid.uuid4()), "role": role},
                            settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.fixture(autouse=True)
def _override_get_db(engine_db_session):
    async def _get_db_override():
        yield engine_db_session
    app.dependency_overrides[get_db] = _get_db_override
    yield
    app.dependency_overrides.pop(get_db, None)


async def _departments_shadow_table(db, dept_id: uuid.UUID) -> None:
    await db.execute(sa.text("DROP TABLE IF EXISTS departments CASCADE"))
    await db.execute(sa.text(
        "CREATE TABLE departments (id uuid PRIMARY KEY, code varchar(50),"
        " name varchar(255), is_active boolean NOT NULL DEFAULT true)"))
    await db.execute(sa.text(
        "INSERT INTO departments (id, code, name, is_active)"
        " VALUES (:i,'DRS','Resync Dept',true)"), {"i": str(dept_id)})
    await db.commit()


async def _pr_awaiting_dept_manager(db, requester: User) -> PurchaseRequest:
    pr = PurchaseRequest(
        number=f"PR-RSY-{uuid.uuid4().hex[:4]}",
        title="Routing resync test",
        status="draft",
        approval_step_idx=0,
        amount=Decimal("100.00"),
        created_by=requester.id,
    )
    db.add(pr)
    await db.flush()
    await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")
    return pr


async def _open_approve_task(db, pr: PurchaseRequest) -> Task:
    return (await db.execute(select(Task).where(
        Task.document_id == pr.id, Task.type == "approve_pr",
        Task.is_completed.is_(False)))).scalar_one()


async def test_put_routing_repoints_task_at_the_current_dept_manager(engine_db_session):
    """The swap that caused the incident: manager A → manager B while a PR is
    in-flight. Saving routing config must hand the open task to B."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    await _departments_shadow_table(db, dept_id)
    old_mgr = User(full_name="Test User", id=uuid.uuid4(), role="dept_manager", department_id=dept_id, is_active=True)
    new_mgr = User(full_name="Test User", id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
    requester = User(full_name="Test User", id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
    db.add_all([old_mgr, new_mgr, requester])
    await db.flush()

    pr = await _pr_awaiting_dept_manager(db, requester)
    assert (await _open_approve_task(db, pr)).assigned_user_id == old_mgr.id

    # The swap — exactly what Portal Admin does to users.role.
    old_mgr.role = "requester"
    new_mgr.role = "dept_manager"
    await db.flush()

    body = {"departments": [{"dept_id": str(dept_id), "gm_or_opm": "gm",
                             "director_user_id": None, "supervisor_enabled": False}],
            "backups": {}}
    async with _client() as c:
        r = await c.put(f"{BASE}/routing", json=body)

    assert r.status_code == 200
    assert (await _open_approve_task(db, pr)).assigned_user_id == new_mgr.id


async def test_put_routing_reports_what_the_resync_touched(engine_db_session):
    """The admin saving the config must see the resync outcome in the response,
    so a silent failure can't masquerade as success."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    await _departments_shadow_table(db, dept_id)
    old_mgr = User(full_name="Test User", id=uuid.uuid4(), role="dept_manager", department_id=dept_id, is_active=True)
    new_mgr = User(full_name="Test User", id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
    requester = User(full_name="Test User", id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
    db.add_all([old_mgr, new_mgr, requester])
    await db.flush()
    pr = await _pr_awaiting_dept_manager(db, requester)
    old_mgr.role = "requester"
    new_mgr.role = "dept_manager"
    await db.flush()

    body = {"departments": [{"dept_id": str(dept_id), "gm_or_opm": "gm",
                             "director_user_id": None, "supervisor_enabled": False}],
            "backups": {}}
    async with _client() as c:
        r = await c.put(f"{BASE}/routing", json=body)

    assert r.json()["routing_resync"] == {"resynced": 1, "errors": 0}


async def test_put_routing_survives_a_failing_resync(engine_db_session, monkeypatch):
    """A resync blowing up must not undo the config the admin just saved — the
    config write is the primary operation, the resync is best-effort."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    await _departments_shadow_table(db, dept_id)

    import app.api.v1.routing as routing_api

    async def _boom(_db):
        raise RuntimeError("resync exploded")

    monkeypatch.setattr(routing_api, "resync_inflight_approvals", _boom)

    body = {"departments": [{"dept_id": str(dept_id), "gm_or_opm": "opm",
                             "director_user_id": None, "supervisor_enabled": True}],
            "backups": {}}
    async with _client() as c:
        r = await c.put(f"{BASE}/routing", json=body)

    assert r.status_code == 200
    assert r.json()["routing_resync"]["error"].startswith("RuntimeError")
    saved = (await db.execute(sa.text(
        "SELECT gm_or_opm FROM approval_dept_routing WHERE dept_id = :d"),
        {"d": str(dept_id)})).scalar()
    assert saved == "opm", "the config write must survive a failed resync"
