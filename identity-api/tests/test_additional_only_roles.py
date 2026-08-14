"""Additional-only roles may never be someone's PRIMARY (login) role.

`erp_pa_officer` and `payment_officer` are granted per-user through identity's
`user_roles` side table. The convention that they are "never a base login role"
used to live only in docstrings and in two frontend hardcoded sets — nothing in
the backend enforced it, so Portal's Access Control Primary Role dropdown (and
any direct API call) could set them as `users.role`. For `payment_officer` that
mattered: finance-api's `_check_can_pay` short-circuits on the PRIMARY role, so
a user set up that way got full payment authority while bypassing the whole
additional-role model.

The rule is now data-driven: `role_defs.assignable_as_primary`. Frontends filter
their dropdowns by it and `put_user_roles` rejects a primary that has it false.
"""
import uuid

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.core.security import create_access_token
from app.main import app
from scripts.seed_authz import seed_authz

pytestmark = pytest.mark.asyncio
BASE = "/identity/v1"


@pytest.fixture
async def seeded(db_session):
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS role_permissions jsonb DEFAULT '{}'::jsonb"))
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS custom_roles jsonb DEFAULT '[]'::jsonb"))
    await seed_authz(db_session)
    # payment_officer is seeded by migration 0008 in real deployments; the test
    # DB is built with create_all, so add it here the same way the migration does.
    await db_session.execute(sa.text(
        "INSERT INTO role_defs(code,label,sort,is_active,assignable_as_primary) "
        "VALUES ('payment_officer','Payment Officer',18,true,false) "
        "ON CONFLICT (code) DO NOTHING"))
    await db_session.commit()
    yield


@pytest.fixture
async def target_user(test_engine):
    from tests.conftest import make_user
    u = await make_user(test_engine, role="requester")
    return u.id


def _admin():
    token = create_access_token(str(uuid.uuid4()), "system_admin")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def test_defs_expose_assignable_as_primary(seeded):
    async with _admin() as c:
        r = await c.get(f"{BASE}/authz/defs")
    assert r.status_code == 200
    roles = {row["code"]: row for row in r.json()["roles"]}
    assert roles["erp_pa_officer"]["assignable_as_primary"] is False
    assert roles["payment_officer"]["assignable_as_primary"] is False
    assert roles["requester"]["assignable_as_primary"] is True
    assert roles["ap_clerk"]["assignable_as_primary"] is True


@pytest.mark.parametrize("code", ["erp_pa_officer", "payment_officer"])
async def test_additional_only_role_rejected_as_primary(seeded, target_user, code):
    async with _admin() as c:
        r = await c.put(f"{BASE}/authz/users/{target_user}/roles",
                        json={"primary": code, "additional": []})
    assert r.status_code == 422
    assert code in str(r.json()["detail"])


@pytest.mark.parametrize("code", ["erp_pa_officer", "payment_officer"])
async def test_additional_only_role_still_allowed_as_additional(seeded, target_user, code, db_session):
    async with _admin() as c:
        r = await c.put(f"{BASE}/authz/users/{target_user}/roles",
                        json={"primary": "requester", "additional": [code]})
    assert r.status_code == 204
    held = (await db_session.execute(sa.text(
        "SELECT role_code FROM user_roles WHERE user_id = :u"), {"u": str(target_user)})).scalars().all()
    assert held == [code]


async def test_normal_role_still_settable_as_primary(seeded, target_user, db_session):
    async with _admin() as c:
        r = await c.put(f"{BASE}/authz/users/{target_user}/roles",
                        json={"primary": "ap_clerk", "additional": []})
    assert r.status_code == 204
    role = (await db_session.execute(sa.text(
        "SELECT role FROM users WHERE id = :u"), {"u": str(target_user)})).scalar_one()
    assert role == "ap_clerk"
