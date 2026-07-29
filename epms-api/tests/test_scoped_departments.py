"""Scoped-department source for the PR list Department filter + Requester picker.

Covers the three fixes:
  1. GET /config/me/scoped-departments returns exactly the caller's scoped
     departments (Director → his directed departments; unrestricted role → all).
  2. GET /users/directory?department_ids=A&department_ids=B filters across
     MULTIPLE departments (the Requester picker for a multi-department Director).
  3. get_for_role never broadcasts a NULL-assignee director/supervisor task to
     every holder (the cross-department approval-task leak).
"""
import uuid
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.schemas.auth import RegisterRequest


async def _make_user(test_engine, role: str, department_id: str | None = None) -> tuple[str, str]:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(
            db,
            RegisterRequest(
                email=f"{role}-{uuid.uuid4().hex[:6]}@scopedept-test.com",
                password="TestPass1!",
                full_name=f"ScopeDept {role}",
                role=role,
            ),
        )
        if department_id is not None:
            user.department_id = uuid.UUID(department_id)
        await db.commit()
    token = create_access_token(str(user.id), user.role)
    return str(user.id), token


async def _make_dept(test_engine, code: str | None = None) -> str:
    from app.models.department import Department

    code = code or f"D{uuid.uuid4().hex[:4].upper()}"
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        dept = Department(code=code, name=f"Dept {code}", is_active=True)
        db.add(dept)
        await db.commit()
        await db.refresh(dept)
        return str(dept.id)


async def _ensure_routing_table(factory) -> None:
    """approval_dept_routing is owned by approval-api's alembic head, so epms_test
    may not have it. Create it on demand (schema copied from
    approval-api/alembic/versions/0001_approval_routing.py), matching the other
    scoping tests."""
    async with factory() as db:
        await db.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS approval_dept_routing ("
            " dept_id uuid PRIMARY KEY,"
            " gm_or_opm varchar(3) NOT NULL DEFAULT 'gm',"
            " director_user_id uuid NULL,"
            " supervisor_enabled boolean NOT NULL DEFAULT false,"
            " updated_by uuid NULL,"
            " updated_at timestamptz NOT NULL DEFAULT now()"
            ")"
        ))
        await db.commit()


async def _map_director(factory, dept_id: str, director_uid: str) -> None:
    async with factory() as db:
        await db.execute(sa.text("DELETE FROM approval_dept_routing WHERE dept_id = :d"), {"d": dept_id})
        await db.execute(sa.text(
            "INSERT INTO approval_dept_routing (dept_id, gm_or_opm, director_user_id, supervisor_enabled) "
            "VALUES (:d, 'gm', :dir, false)"),
            {"d": dept_id, "dir": director_uid})
        await db.commit()


def _authed_client(token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


# ── 1. GET /config/me/scoped-departments ─────────────────────────────────────

@pytest.mark.asyncio
async def test_director_scoped_departments_are_his_directed_depts_only(test_engine):
    """A Director's scoped-departments = exactly the departments he directs
    (approval_dept_routing.director_user_id), NOT a single JWT department and NOT
    the whole company list. This is the fix for the Requester/Department pickers
    showing only the Director's own primary department."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    await _ensure_routing_table(factory)

    dept_a = await _make_dept(test_engine)
    dept_b = await _make_dept(test_engine)
    dept_unrelated = await _make_dept(test_engine)

    # Director's own primary department is dept_a, but he directs BOTH a and b.
    uid_dir, tok_dir = await _make_user(test_engine, "director", department_id=dept_a)
    await _map_director(factory, dept_a, uid_dir)
    await _map_director(factory, dept_b, uid_dir)

    async with _authed_client(tok_dir) as c:
        resp = await c.get("/api/v1/config/me/scoped-departments")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["unrestricted"] is False
        ids = {d["id"] for d in body["items"]}
        assert ids == {dept_a, dept_b}, (
            f"Director scoped-departments should be exactly his 2 directed depts, got {ids}"
        )
        assert dept_unrelated not in ids


@pytest.mark.asyncio
async def test_unrestricted_role_scoped_departments_unrestricted_flag(test_engine):
    """A finance_manager (unrestricted) gets unrestricted=True and the full active
    department list."""
    dept_x = await _make_dept(test_engine)
    _, tok_fm = await _make_user(test_engine, "finance_manager")

    async with _authed_client(tok_fm) as c:
        resp = await c.get("/api/v1/config/me/scoped-departments")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["unrestricted"] is True
        ids = {d["id"] for d in body["items"]}
        assert dept_x in ids, "unrestricted role should see every department"


@pytest.mark.asyncio
async def test_requester_scoped_departments_is_own_department(test_engine):
    """A requester's scoped set is their own department."""
    dept_r = await _make_dept(test_engine)
    dept_other = await _make_dept(test_engine)
    _, tok_req = await _make_user(test_engine, "requester", department_id=dept_r)

    async with _authed_client(tok_req) as c:
        resp = await c.get("/api/v1/config/me/scoped-departments")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["unrestricted"] is False
        ids = {d["id"] for d in body["items"]}
        assert ids == {dept_r}
        assert dept_other not in ids


# ── 2. GET /users/directory?department_ids= (multi-department) ────────────────

@pytest.mark.asyncio
async def test_directory_department_ids_filters_multiple_departments(test_engine):
    """The Requester picker for a multi-department Director must be able to fetch
    requesters across SEVERAL departments in one call. department_ids (repeatable)
    returns users from any listed department and excludes the rest."""
    dept_a = await _make_dept(test_engine)
    dept_b = await _make_dept(test_engine)
    dept_c = await _make_dept(test_engine)

    uid_a, _ = await _make_user(test_engine, "requester", department_id=dept_a)
    uid_b, _ = await _make_user(test_engine, "requester", department_id=dept_b)
    uid_c, _ = await _make_user(test_engine, "requester", department_id=dept_c)

    # Any authenticated caller can hit the directory.
    _, tok = await _make_user(test_engine, "procurement_officer")
    async with _authed_client(tok) as c:
        resp = await c.get(
            "/api/v1/users/directory",
            params=[("role", "requester"), ("department_ids", dept_a), ("department_ids", dept_b)],
        )
        assert resp.status_code == 200, resp.text
        got = {u["id"] for u in resp.json()["items"]}
        assert uid_a in got and uid_b in got, "department_ids should include A and B requesters"
        assert uid_c not in got, "department_ids must exclude requesters outside the listed departments"


@pytest.mark.asyncio
async def test_directory_department_ids_takes_precedence_over_single(test_engine):
    """When both are supplied, department_ids wins over the single department_id."""
    dept_a = await _make_dept(test_engine)
    dept_b = await _make_dept(test_engine)
    uid_a, _ = await _make_user(test_engine, "requester", department_id=dept_a)
    uid_b, _ = await _make_user(test_engine, "requester", department_id=dept_b)

    _, tok = await _make_user(test_engine, "procurement_officer")
    async with _authed_client(tok) as c:
        resp = await c.get(
            "/api/v1/users/directory",
            params=[("role", "requester"), ("department_id", dept_a), ("department_ids", dept_b)],
        )
        assert resp.status_code == 200, resp.text
        got = {u["id"] for u in resp.json()["items"]}
        assert uid_b in got and uid_a not in got


# ── 3. Inbox: no cross-department director broadcast ──────────────────────────

async def _insert_task(factory, *, assigned_role: str, assigned_user_id: str | None) -> str:
    from app.models.task import Task
    async with factory() as db:
        t = Task(
            type="approve_pr",
            document_type="pr",
            document_id=uuid.uuid4(),
            document_number=f"PR-{uuid.uuid4().hex[:8]}",
            assigned_role=assigned_role,
            assigned_user_id=uuid.UUID(assigned_user_id) if assigned_user_id else None,
            title="Approve PR",
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return str(t.id)


@pytest.mark.asyncio
async def test_null_assignee_director_task_does_not_broadcast(test_engine):
    """A NULL-assignee director task is an anomaly and must NOT appear in every
    director's inbox — that was the cross-department approval-task leak. A director
    task assigned specifically to this director MUST still appear."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    await _ensure_routing_table(factory)

    dept_d = await _make_dept(test_engine)
    uid_dir, tok_dir = await _make_user(test_engine, "director", department_id=dept_d)
    await _map_director(factory, dept_d, uid_dir)  # derives the 'director' role code

    leaked = await _insert_task(factory, assigned_role="director", assigned_user_id=None)
    mine = await _insert_task(factory, assigned_role="director", assigned_user_id=uid_dir)

    async with _authed_client(tok_dir) as c:
        resp = await c.get("/api/v1/tasks")
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()["items"]}
        assert leaked not in ids, "NULL-assignee director task leaked into the inbox (broadcast)"
        assert mine in ids, "director's own specifically-assigned task must still appear"


@pytest.mark.asyncio
async def test_null_assignee_pool_task_still_broadcasts(test_engine):
    """Regression guard: excluding director/supervisor must NOT break genuine
    pool-role broadcast. A NULL-assignee gm task still reaches a gm holder."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    uid_gm, tok_gm = await _make_user(test_engine, "gm")

    pooled = await _insert_task(factory, assigned_role="gm", assigned_user_id=None)

    async with _authed_client(tok_gm) as c:
        resp = await c.get("/api/v1/tasks")
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()["items"]}
        assert pooled in ids, "NULL-assignee gm (pool) task should still broadcast to a gm"
