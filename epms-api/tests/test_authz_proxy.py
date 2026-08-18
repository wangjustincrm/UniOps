"""epms authz endpoints: direct-DB matrix reads + identity write forwarding.

Phase 2 (Task 3) retired the Phase 1 authz_client apparatus (HTTP + 60 s
cache + identity-outage fallback + write-through mirror into
company_config.role_permissions). GET /config/role-permissions and GET
/config/me/permissions now read identity's role_defs / permission_defs /
role_permissions / role_permission_locks tables directly via the shared
uniops_authz package — same physical DB, no HTTP, no token, no dependency on
the identity *service* being up. Only PATCH /config/role-permissions still
forwards to identity (it owns lock-cell validation and the audit columns).
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import config as config_api
from app.core.security import decode_token

pytestmark = pytest.mark.asyncio


def _client_user_id(client) -> uuid.UUID:
    """Pull the caller's user id out of their own Bearer token (no endpoint
    exposes 'who am I' as a raw id, so decode it locally instead)."""
    token = client.headers["Authorization"].removeprefix("Bearer ")
    return uuid.UUID(decode_token(token)["sub"])


async def _seed(factory, *, roles=(), perms=(), grants=(), locks=(), user_roles=()):
    """roles: [(code, sort)]; perms: [(key, sort)]; grants/locks: [(role, key)];
    user_roles: [(user_id, role_code)] — additional roles for a specific user."""
    async with factory() as db:
        for code, sort in roles:
            await db.execute(text(
                "INSERT INTO role_defs (code, label, sort, is_active) VALUES (:c, :c, :s, true)"),
                {"c": code, "s": sort})
        for key, sort in perms:
            await db.execute(text(
                "INSERT INTO permission_defs (key, module, label, sort) VALUES (:k, 'test', :k, :s)"),
                {"k": key, "s": sort})
        for role, key in grants:
            await db.execute(text(
                "INSERT INTO role_permissions (role_code, permission_key) VALUES (:r, :k)"),
                {"r": role, "k": key})
        for role, key in locks:
            await db.execute(text(
                "INSERT INTO role_permission_locks (role_code, permission_key) VALUES (:r, :k)"),
                {"r": role, "k": key})
        for uid, code in user_roles:
            await db.execute(text(
                "INSERT INTO user_roles (user_id, role_code) VALUES (:u, :c)"),
                {"u": str(uid), "c": code})
        await db.commit()


@pytest.fixture
async def authz_factory(test_engine):
    """Session factory + a clean slate for the 4 identity authz tables. This
    module is the only test module that seeds them (no other test currently
    depends on non-empty role_permissions/permission_defs/role_defs), so a
    blanket DELETE per test is enough isolation."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        for tbl in ("role_permission_locks", "role_permissions", "permission_defs", "role_defs"):
            await db.execute(text(f"DELETE FROM {tbl}"))
        await db.commit()
    yield factory


# ── GET /role-permissions: direct-read matrix, old shape preserved ─────────
# (booking's sidebar still reads this exact endpoint/shape)

async def test_get_role_permissions_matches_matrix_shape(admin_client, authz_factory):
    """{role: {key: bool}} for every role x every permission key; a cell is
    True if granted OR locked (a lock is a forced grant)."""
    await _seed(
        authz_factory,
        roles=[("requester", 0), ("finance_manager", 1)],
        perms=[("view_pr", 0), ("view_pa", 1)],
        grants=[("finance_manager", "view_pa")],
        locks=[("requester", "view_pr")],
    )
    r = await admin_client.get("/api/v1/config/role-permissions")
    assert r.status_code == 200
    assert r.json() == {
        "requester": {"view_pr": True, "view_pa": False},
        "finance_manager": {"view_pr": False, "view_pa": True},
    }


async def test_role_permissions_survives_identity_down(admin_client, authz_factory, mocker):
    """The new guarantee this phase buys: this GET no longer calls identity at
    all, so it keeps working even while identity-api is completely down."""
    mocker.patch.object(
        config_api, "_forward_identity",
        mocker.AsyncMock(side_effect=RuntimeError("identity is down")),
    )
    await _seed(authz_factory, roles=[("requester", 0)], perms=[("view_pr", 0)],
                grants=[("requester", "view_pr")])
    r = await admin_client.get("/api/v1/config/role-permissions")
    assert r.status_code == 200
    assert r.json() == {"requester": {"view_pr": True}}


# ── GET /me/permissions: union across base + additional roles ─────────────

async def test_me_permissions_unions_additional_roles(finance_client, authz_factory):
    """finance_manager (base) + finance_bp (additional, via identity's
    user_roles) — a permission granted to EITHER role must read True."""
    uid = _client_user_id(finance_client)
    await _seed(
        authz_factory,
        roles=[("finance_manager", 0), ("finance_bp", 1)],
        perms=[("view_pa", 0), ("view_budget_plans", 1)],
        grants=[("finance_manager", "view_pa"), ("finance_bp", "view_budget_plans")],
        user_roles=[(uid, "finance_bp")],
    )
    r = await finance_client.get("/api/v1/config/me/permissions")
    assert r.status_code == 200
    body = r.json()
    assert body["permissions"]["view_pa"] is True             # from base role
    assert body["permissions"]["view_budget_plans"] is True   # from additional role
    assert body["roles"] == ["finance_manager", "finance_bp"]


async def test_me_permissions_ungranted_key_is_false_not_missing(finance_client, authz_factory):
    await _seed(authz_factory, roles=[("finance_manager", 0)], perms=[("view_pa", 0)])
    r = await finance_client.get("/api/v1/config/me/permissions")
    assert r.status_code == 200
    assert r.json()["permissions"]["view_pa"] is False


async def test_me_permissions_survives_identity_down(finance_client, authz_factory, mocker):
    """Same new guarantee as GET /role-permissions: no HTTP call to identity."""
    mocker.patch.object(
        config_api, "_forward_identity",
        mocker.AsyncMock(side_effect=RuntimeError("identity is down")),
    )
    await _seed(authz_factory, roles=[("finance_manager", 0)], perms=[("view_pa", 0)],
                grants=[("finance_manager", "view_pa")])
    r = await finance_client.get("/api/v1/config/me/permissions")
    assert r.status_code == 200
    assert r.json()["permissions"]["view_pa"] is True


# ── PATCH /role-permissions: still forwards to identity ────────────────────

async def test_patch_forwards_to_identity(admin_client, mocker):
    fwd = mocker.patch.object(
        config_api, "_forward_identity",
        mocker.AsyncMock(return_value=(200, {"requester": {"view_pr": False}})),
    )
    r = await admin_client.patch(
        "/api/v1/config/role-permissions", json={"requester": {"view_pr": False}}
    )
    assert r.status_code == 200
    assert fwd.await_args.args[0] == "PATCH"
    assert fwd.await_args.args[1] == "/authz/matrix"
    assert fwd.await_args.kwargs["json"] == {"changes": {"requester": {"view_pr": False}}}


async def test_patch_409_propagates(admin_client, mocker):
    """Locked-cell conflict from identity must pass through as-is."""
    mocker.patch.object(
        config_api, "_forward_identity",
        mocker.AsyncMock(return_value=(
            409, {"detail": {"locked": [{"role": "requester", "key": "view_pr"}]}}
        )),
    )
    r = await admin_client.patch(
        "/api/v1/config/role-permissions", json={"requester": {"view_pr": False}}
    )
    assert r.status_code == 409


async def test_patch_422_propagates(admin_client, mocker):
    """Unknown role/permission from identity must pass through as-is."""
    mocker.patch.object(
        config_api, "_forward_identity",
        mocker.AsyncMock(return_value=(422, {"detail": "Unknown role 'bogus'"})),
    )
    r = await admin_client.patch(
        "/api/v1/config/role-permissions", json={"bogus": {"view_pr": True}}
    )
    assert r.status_code == 422


async def test_patch_502_when_identity_down(admin_client, mocker):
    mocker.patch.object(
        config_api, "_forward_identity", mocker.AsyncMock(side_effect=RuntimeError("down")),
    )
    r = await admin_client.patch(
        "/api/v1/config/role-permissions", json={"requester": {"view_pr": False}}
    )
    assert r.status_code == 502


async def test_patch_no_longer_writes_local_mirror(admin_client, mocker, test_engine):
    """Phase 1's write-through into company_config.role_permissions is
    retired — a successful PATCH must NOT touch that JSONB anymore."""
    from app.crud import config as config_crud
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        cfg = await config_crud.get_or_create(db)
        before = config_crud.get_effective_role_permissions(cfg)["requester"]["view_pr"]
        await db.commit()

    mocker.patch.object(
        config_api, "_forward_identity",
        mocker.AsyncMock(return_value=(200, {"requester": {"view_pr": not before}})),
    )
    r = await admin_client.patch(
        "/api/v1/config/role-permissions", json={"requester": {"view_pr": not before}}
    )
    assert r.status_code == 200

    async with factory() as db:
        cfg = await config_crud.get_or_create(db)
        after = config_crud.get_effective_role_permissions(cfg)["requester"]["view_pr"]
    assert after == before   # untouched — nothing writes this column anymore


# ── GET /user-roles passthrough (unaffected by this phase) ─────────────────

async def test_get_user_roles_proxies_identity(admin_client, mocker):
    fake_body = {"user_roles": {"11111111-1111-1111-1111-111111111111": ["auditor", "cfo"]}}
    fwd = mocker.patch.object(
        config_api, "_forward_identity", mocker.AsyncMock(return_value=(200, fake_body)),
    )
    r = await admin_client.get("/api/v1/config/user-roles")
    assert r.status_code == 200
    assert r.json() == fake_body
    assert fwd.await_args.args[0] == "GET"
    assert fwd.await_args.args[1] == "/authz/user-roles"


async def test_custom_role_crud_removed(admin_client):
    r = await admin_client.post("/api/v1/config/roles", json={"code": "x", "label": "X"})
    assert r.status_code == 405


# ── GET /me/permissions: real 4xx from identity must not apply here anymore ─
# (this endpoint no longer calls identity at all — see the direct-read tests
# above; kept here only as a guard against an accidental future regression
# reintroducing an HTTP call on this path.)

async def test_me_permissions_ignores_forward_mock_entirely(finance_client, authz_factory, mocker):
    fwd = mocker.patch.object(config_api, "_forward_identity", mocker.AsyncMock())
    await _seed(authz_factory, roles=[("finance_manager", 0)], perms=[("view_pa", 0)])
    r = await finance_client.get("/api/v1/config/me/permissions")
    assert r.status_code == 200
    fwd.assert_not_awaited()


# ── Role-change resync outcome must reach the caller ──────────────────────────
#
# identity now answers PUT /authz/users/{id}/roles with 200 + {"routing_resync":
# ...} — the outcome of re-pointing in-flight approvals at the new role holder
# (prod incident 2026-08-18). This proxy is the only path Portal Admin has to
# that endpoint, so swallowing the body would make a failed resync invisible
# exactly where an admin could act on it.

async def test_put_user_roles_passes_the_resync_outcome_back(admin_client, monkeypatch):
    async def _fake_forward(method, path, token, json=None):
        return 200, {"routing_resync": "failed: approval-api down"}

    monkeypatch.setattr(config_api, "_forward_identity", _fake_forward)
    r = await admin_client.put(f"/api/v1/config/users/{uuid.uuid4()}/roles",
                               json={"primary": "requester", "additional": []})

    assert r.status_code == 200, r.text
    assert r.json()["routing_resync"] == "failed: approval-api down"
