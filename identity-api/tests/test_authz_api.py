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
    # Add the role_permissions and custom_roles columns that seed_authz expects
    # (they exist in the physical epms table but not in CompanyConfig model)
    # This is a test-only workaround; production epms table has these columns
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS role_permissions jsonb DEFAULT '{}'::jsonb"))
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS custom_roles jsonb DEFAULT '[]'::jsonb"))
    await seed_authz(db_session)
    await db_session.commit()
    yield


def _client(role="system_admin", sub=None):
    token = create_access_token(sub or str(uuid.uuid4()), role)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def test_matrix_shape(seeded):
    async with _client("requester") as c:
        r = await c.get(f"{BASE}/authz/matrix")
    assert r.status_code == 200
    m = r.json()
    assert m["requester"]["view_pr"] is True     # locked
    assert m["system_admin"]["admin_panel"] is True


async def test_patch_requires_admin(seeded):
    async with _client("requester") as c:
        r = await c.patch(f"{BASE}/authz/matrix",
                          json={"changes": {"auditor": {"view_pr": False}}})
    assert r.status_code == 403


async def test_patch_toggles_and_rejects_locked(seeded):
    async with _client() as c:
        r = await c.patch(f"{BASE}/authz/matrix",
                          json={"changes": {"auditor": {"view_pr": False}}})
        assert r.status_code == 200
        assert r.json()["auditor"]["view_pr"] is False
        r2 = await c.patch(f"{BASE}/authz/matrix",
                           json={"changes": {"requester": {"view_pr": False}}})
        assert r2.status_code == 409
        assert r2.json()["detail"]["locked"] == [{"role": "requester", "key": "view_pr"}]
        r3 = await c.patch(f"{BASE}/authz/matrix",
                           json={"changes": {"nosuch": {"view_pr": True}}})
        assert r3.status_code == 422


async def test_me_permissions_union(seeded, db_session):
    uid = uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role, is_active, mfa_enabled, must_change_password, notification_channel, erp_imported) "
        "VALUES (:i, :e, 'x', 'T User', 'warehouse_staff', true, false, false, 'email_only', false)"),
        {"i": str(uid), "e": f"{uid}@t.co"})
    await db_session.execute(sa.text(
        "INSERT INTO user_roles(user_id, role_code) VALUES (:i, 'ap_clerk')"),
        {"i": str(uid)})
    await db_session.commit()
    async with _client("warehouse_staff", str(uid)) as c:
        r = await c.get(f"{BASE}/me/permissions")
    body = r.json()
    assert body["roles"] == ["warehouse_staff", "ap_clerk"]
    assert body["permissions"]["view_gr"] is True       # from primary
    assert body["permissions"]["view_invoice"] is True  # from additional (union)


async def test_get_user_roles_requires_admin(seeded):
    async with _client("requester") as c:
        r = await c.get(f"{BASE}/authz/user-roles")
    assert r.status_code == 403


async def test_get_user_roles_maps_by_user(seeded, db_session):
    uid = uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role, is_active, mfa_enabled, must_change_password, notification_channel, erp_imported) "
        "VALUES (:i, :e, 'x', 'T3', 'requester', true, false, false, 'email_only', false)"),
        {"i": str(uid), "e": f"{uid}@t.co"})
    await db_session.execute(sa.text(
        "INSERT INTO user_roles(user_id, role_code) VALUES (:i, 'cfo'), (:i, 'auditor')"),
        {"i": str(uid)})
    await db_session.commit()
    async with _client() as c:
        r = await c.get(f"{BASE}/authz/user-roles")
    assert r.status_code == 200
    body = r.json()["user_roles"]
    assert body[str(uid)] == ["auditor", "cfo"]


async def test_put_user_roles_transactional(seeded, db_session):
    uid = uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role, is_active, mfa_enabled, must_change_password, notification_channel, erp_imported) "
        "VALUES (:i, :e, 'x', 'T2', 'requester', true, false, false, 'email_only', false)"), {"i": str(uid), "e": f"{uid}@t.co"})
    await db_session.commit()
    async with _client() as c:
        r = await c.put(f"{BASE}/authz/users/{uid}/roles",
                        json={"primary": "auditor", "additional": ["cfo", "vendor_manager"]})
        assert r.status_code == 204
        r2 = await c.put(f"{BASE}/authz/users/{uid}/roles",
                         json={"primary": "nosuch", "additional": []})
        assert r2.status_code == 422
    role = (await db_session.execute(sa.text(
        "SELECT role FROM users WHERE id=:i"), {"i": str(uid)})).scalar_one()
    assert role == "auditor"
    add = (await db_session.execute(sa.text(
        "SELECT role_code FROM user_roles WHERE user_id=:i ORDER BY role_code"),
        {"i": str(uid)})).scalars().all()
    assert add == ["cfo", "vendor_manager"]


POST_ROLES = ("gm", "opm", "vendor_manager", "finance_manager", "procurement_manager")


async def test_post_role_singleton_conflicts(seeded, db_session):
    """gm/opm/... may be held by exactly one user — as primary OR additional."""
    a, b = uuid.uuid4(), uuid.uuid4()
    for i, uid in enumerate((a, b)):
        await db_session.execute(sa.text(
            "INSERT INTO users (id, email, hashed_password, full_name, role, is_active, mfa_enabled, must_change_password, notification_channel, erp_imported) "
            "VALUES (:i, :e, 'x', 'U', 'requester', true, false, false, 'email_only', false)"), {"i": str(uid), "e": f"{uid}@t.co"})
    await db_session.commit()
    async with _client() as c:
        r1 = await c.put(f"{BASE}/authz/users/{a}/roles",
                         json={"primary": "requester", "additional": ["gm"]})
        assert r1.status_code == 204
        # second user cannot also hold gm
        r2 = await c.put(f"{BASE}/authz/users/{b}/roles",
                         json={"primary": "requester", "additional": ["gm"]})
        assert r2.status_code == 409
        assert r2.json()["detail"]["conflict"]["role"] == "gm"
        assert r2.json()["detail"]["conflict"]["held_by"] == str(a)
        # finance_bp is NOT a singleton — both may hold it
        assert (await c.put(f"{BASE}/authz/users/{a}/roles",
                            json={"primary": "requester", "additional": ["finance_bp"]})).status_code == 204
        assert (await c.put(f"{BASE}/authz/users/{b}/roles",
                            json={"primary": "requester", "additional": ["finance_bp"]})).status_code == 204


async def test_post_role_singleton_checks_primary_role_too(seeded, db_session):
    """A post held as someone's PRIMARY role also blocks it as another's additional."""
    a, b = uuid.uuid4(), uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role, is_active, mfa_enabled, must_change_password, notification_channel, erp_imported) "
        "VALUES (:i, :e, 'x', 'Primary GM', 'gm', true, false, false, 'email_only', false)"), {"i": str(a), "e": f"{a}@t.co"})
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role, is_active, mfa_enabled, must_change_password, notification_channel, erp_imported) "
        "VALUES (:i, :e, 'x', 'Other', 'requester', true, false, false, 'email_only', false)"), {"i": str(b), "e": f"{b}@t.co"})
    await db_session.commit()
    async with _client() as c:
        r = await c.put(f"{BASE}/authz/users/{b}/roles",
                        json={"primary": "requester", "additional": ["gm"]})
    assert r.status_code == 409
    assert r.json()["detail"]["conflict"]["held_by"] == str(a)
    # cleanup: `a`'s primary role was set via raw SQL (bypassing the API), so
    # nothing else releases its permanent claim on 'gm' — this test DB/engine
    # is session-scoped with no reset between tests, so a stale claim here
    # would 409 every later test that touches 'gm'.
    await db_session.execute(sa.text("DELETE FROM users WHERE id IN (:a, :b)"),
                             {"a": str(a), "b": str(b)})
    await db_session.commit()


async def test_setting_own_post_again_is_idempotent(seeded, db_session):
    """Re-saving the same user's own post must not 409 against itself."""
    a = uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role, is_active, mfa_enabled, must_change_password, notification_channel, erp_imported) "
        "VALUES (:i, :e, 'x', 'U', 'requester', true, false, false, 'email_only', false)"), {"i": str(a), "e": f"{a}@t.co"})
    await db_session.commit()
    async with _client() as c:
        assert (await c.put(f"{BASE}/authz/users/{a}/roles",
                            json={"primary": "requester", "additional": ["gm"]})).status_code == 204
        assert (await c.put(f"{BASE}/authz/users/{a}/roles",
                            json={"primary": "requester", "additional": ["gm", "finance_bp"]})).status_code == 204
