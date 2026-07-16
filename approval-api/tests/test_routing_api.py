"""Routing API: read/write dept rules + backups; admin-gated."""
import uuid

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app

pytestmark = pytest.mark.asyncio
BASE = "/approval/v1"


def _token(role: str, sub: str | None = None) -> str:
    return jose_jwt.encode(
        {"sub": sub or str(uuid.uuid4()), "role": role},
        settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _client(role="system_admin"):
    """Authed client — NEW infrastructure for approval-api (see task-6 brief)."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {_token(role)}"})


async def _reset_departments_shadow_table(db):
    """approval-api has no ORM model for `departments` (identity/epms-owned
    physical table) — following test_seed_routing.py's shadow-table idiom,
    create a minimal stand-in scoped to what this test needs.
    """
    await db.execute(sa.text("DROP TABLE IF EXISTS departments CASCADE"))
    await db.execute(sa.text(
        "CREATE TABLE departments (id uuid PRIMARY KEY, code varchar(50),"
        " name varchar(255), is_active boolean NOT NULL DEFAULT true)"))
    await db.commit()


@pytest.fixture(autouse=True)
def _override_get_db(engine_db_session):
    async def _get_db_override():
        yield engine_db_session
    app.dependency_overrides[get_db] = _get_db_override
    yield
    app.dependency_overrides.pop(get_db, None)


async def test_get_lists_all_active_departments_with_defaults(engine_db_session):
    db = engine_db_session
    await _reset_departments_shadow_table(db)
    d = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO departments (id, code, name, is_active) VALUES (:i,'D9','Dept 9',true)"),
        {"i": str(d)})
    await db.flush()
    async with _client() as c:
        r = await c.get(f"{BASE}/routing")
    assert r.status_code == 200
    row = next(x for x in r.json()["departments"] if x["dept_id"] == str(d))
    # No routing row yet -> engine defaults surface: gm, no director, NO supervisor
    assert (row["gm_or_opm"], row["director_user_id"], row["supervisor_enabled"]) == ("gm", None, False)


async def test_put_upserts_and_requires_admin(engine_db_session):
    db = engine_db_session
    await _reset_departments_shadow_table(db)
    d = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO departments (id, code, name, is_active) VALUES (:i,'D8','Dept 8',true)"),
        {"i": str(d)})
    await db.flush()
    body = {"departments": [{"dept_id": str(d), "gm_or_opm": "opm",
                             "director_user_id": None, "supervisor_enabled": True}],
            "backups": {"gm": None, "opm": None}}
    async with _client("requester") as c:
        assert (await c.put(f"{BASE}/routing", json=body)).status_code == 403
    async with _client() as c:
        assert (await c.put(f"{BASE}/routing", json=body)).status_code == 200
        bad = {"departments": [{"dept_id": str(d), "gm_or_opm": "ceo",
                                "director_user_id": None, "supervisor_enabled": False}],
               "backups": {"gm": None, "opm": None}}
        assert (await c.put(f"{BASE}/routing", json=bad)).status_code == 422
    got = (await db.execute(sa.text(
        "SELECT gm_or_opm, supervisor_enabled FROM approval_dept_routing WHERE dept_id=:d"),
        {"d": str(d)})).first()
    assert got == ("opm", True)


async def test_put_rejects_unknown_dept_id(engine_db_session):
    db = engine_db_session
    await _reset_departments_shadow_table(db)
    unknown = uuid.uuid4()
    body = {"departments": [{"dept_id": str(unknown), "gm_or_opm": "gm",
                             "director_user_id": None, "supervisor_enabled": False}],
            "backups": {"gm": None, "opm": None}}
    async with _client() as c:
        r = await c.put(f"{BASE}/routing", json=body)
    assert r.status_code == 422


async def test_put_upserts_backups_and_get_reflects_them(engine_db_session):
    db = engine_db_session
    await _reset_departments_shadow_table(db)
    gm_backup, opm_backup = uuid.uuid4(), uuid.uuid4()
    body = {"departments": [], "backups": {"gm": str(gm_backup), "opm": str(opm_backup)}}
    async with _client() as c:
        r = await c.put(f"{BASE}/routing", json=body)
        assert r.status_code == 200
        assert r.json()["backups"] == {"gm": str(gm_backup), "opm": str(opm_backup)}

        # Clearing a backup (null) removes the row entirely.
        r2 = await c.put(f"{BASE}/routing", json={
            "departments": [], "backups": {"gm": None, "opm": str(opm_backup)}})
        assert r2.status_code == 200
        assert r2.json()["backups"] == {"gm": None, "opm": str(opm_backup)}

    n = (await db.execute(sa.text(
        "SELECT count(*) FROM approval_backups WHERE role_code='gm'"))).scalar_one()
    assert n == 0
