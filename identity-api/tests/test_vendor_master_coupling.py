"""Granting 'vendor_master' must couple-grant 'mdm.vendor.write' (one checkbox,
both gates). See docs/superpowers/specs/2026-08-01-vendor-master-authz-coupling-design.md."""
import uuid

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.core.security import create_access_token
from app.main import app
from scripts.seed_authz import seed_authz
from scripts.seed_phase2_keys import seed_phase2_keys

pytestmark = pytest.mark.asyncio
BASE = "/identity/v1"


@pytest.fixture
async def seeded_full(db_session):
    # seed_authz expects these jsonb columns on company_config (test-only; the
    # physical epms table already has them).
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS role_permissions jsonb DEFAULT '{}'::jsonb"))
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS custom_roles jsonb DEFAULT '[]'::jsonb"))
    await seed_authz(db_session)        # epms keys incl vendor_master + role_defs + defaults
    await seed_phase2_keys(db_session)  # registers mdm.vendor.write permission_def (+ defaults)
    await db_session.commit()
    yield


def _admin_client():
    token = create_access_token(str(uuid.uuid4()), "system_admin")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _has(db_session, role: str, key: str) -> bool:
    return bool((await db_session.execute(sa.text(
        "SELECT 1 FROM role_permissions WHERE role_code=:r AND permission_key=:k"),
        {"r": role, "k": key})).scalar())


async def test_granting_vendor_master_couples_mdm_write(seeded_full, db_session):
    # 'auditor' starts with neither key.
    async with _admin_client() as c:
        r = await c.patch(f"{BASE}/authz/matrix",
                          json={"changes": {"auditor": {"vendor_master": True}}})
        assert r.status_code == 200
    assert await _has(db_session, "auditor", "vendor_master")
    assert await _has(db_session, "auditor", "mdm.vendor.write")


async def test_revoking_vendor_master_revokes_mdm_write(seeded_full, db_session):
    # 'cfo' starts with neither key.
    async with _admin_client() as c:
        await c.patch(f"{BASE}/authz/matrix",
                      json={"changes": {"cfo": {"vendor_master": True}}})
        r = await c.patch(f"{BASE}/authz/matrix",
                          json={"changes": {"cfo": {"vendor_master": False}}})
        assert r.status_code == 200
    assert not await _has(db_session, "cfo", "vendor_master")
    assert not await _has(db_session, "cfo", "mdm.vendor.write")


async def test_other_key_does_not_touch_mdm_write(seeded_full, db_session):
    # 'gm' starts with neither key; toggling an unrelated key must not add it.
    async with _admin_client() as c:
        r = await c.patch(f"{BASE}/authz/matrix",
                          json={"changes": {"gm": {"parts_catalog": True}}})
        assert r.status_code == 200
    assert not await _has(db_session, "gm", "mdm.vendor.write")


async def test_coupling_is_idempotent(seeded_full, db_session):
    # 'dept_admin' starts with neither key.
    async with _admin_client() as c:
        for _ in range(2):
            r = await c.patch(f"{BASE}/authz/matrix",
                              json={"changes": {"dept_admin": {"vendor_master": True}}})
            assert r.status_code == 200
    n = (await db_session.execute(sa.text(
        "SELECT count(*) FROM role_permissions "
        "WHERE role_code='dept_admin' AND permission_key='mdm.vendor.write'"))).scalar_one()
    assert n == 1


async def test_coupling_does_not_bypass_locks(seeded_full, db_session):
    # Lock vendor_master for a role and pre-grant mdm.vendor.write. A delta that
    # tries to REVOKE vendor_master hits the lock check (authz.py 60-63), which
    # 409s the WHOLE patch before the write loop — so the coupling never runs
    # and mdm.vendor.write is NOT removed. 'warehouse_staff' is unused elsewhere
    # in this file, keeping the test self-contained.
    role = "warehouse_staff"
    await db_session.execute(sa.text(
        "INSERT INTO role_permission_locks(role_code,permission_key) "
        "VALUES (:r,'vendor_master') ON CONFLICT DO NOTHING"), {"r": role})
    await db_session.execute(sa.text(
        "INSERT INTO role_permissions(role_code,permission_key) "
        "VALUES (:r,'mdm.vendor.write') ON CONFLICT DO NOTHING"), {"r": role})
    await db_session.commit()
    async with _admin_client() as c:
        r = await c.patch(f"{BASE}/authz/matrix",
                          json={"changes": {role: {"vendor_master": False}}})
        assert r.status_code == 409
    assert await _has(db_session, role, "mdm.vendor.write")  # not revoked
