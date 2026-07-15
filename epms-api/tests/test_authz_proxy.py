"""epms authz proxy: pass-through, cache, and fallback when identity is down."""
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.core.authz_client as ac
from app.crud import config as config_crud

pytestmark = pytest.mark.asyncio

FAKE_MATRIX = {"requester": {"view_pr": True}}


async def test_get_role_permissions_proxies_identity(admin_client, mocker):
    mocker.patch.object(ac, "_fetch_matrix", mocker.AsyncMock(return_value=FAKE_MATRIX))
    ac.invalidate_cache()
    r = await admin_client.get("/api/v1/config/role-permissions")
    assert r.status_code == 200
    assert r.json() == FAKE_MATRIX


async def test_matrix_falls_back_when_identity_down(admin_client, mocker):
    mocker.patch.object(ac, "_fetch_matrix",
                        mocker.AsyncMock(side_effect=RuntimeError("down")))
    ac.invalidate_cache()
    r = await admin_client.get("/api/v1/config/role-permissions")
    assert r.status_code == 200
    body = r.json()                       # frozen JSONB fallback
    assert body["system_admin"]["admin_panel"] is True


async def test_patch_forwards_and_no_local_write(admin_client, mocker):
    fwd = mocker.patch.object(
        ac, "forward", mocker.AsyncMock(return_value=(200, FAKE_MATRIX)))
    r = await admin_client.patch("/api/v1/config/role-permissions",
                                 json={"auditor": {"view_pr": False}})
    assert r.status_code == 200
    assert fwd.await_args.args[0] == "PATCH"
    assert fwd.await_args.kwargs["json"] == {"changes": {"auditor": {"view_pr": False}}}


async def test_patch_502_when_identity_down(admin_client, mocker):
    mocker.patch.object(ac, "forward",
                        mocker.AsyncMock(side_effect=RuntimeError("down")))
    r = await admin_client.patch("/api/v1/config/role-permissions",
                                 json={"auditor": {"view_pr": False}})
    assert r.status_code == 502


# ── GET /user-roles passthrough ─────────────────────────────────────────────

async def test_get_user_roles_proxies_identity(admin_client, mocker):
    fake_body = {"user_roles": {"11111111-1111-1111-1111-111111111111": ["auditor", "cfo"]}}
    fwd = mocker.patch.object(
        ac, "forward", mocker.AsyncMock(return_value=(200, fake_body)))
    r = await admin_client.get("/api/v1/config/user-roles")
    assert r.status_code == 200
    assert r.json() == fake_body
    assert fwd.await_args.args[0] == "GET"
    assert fwd.await_args.args[1] == "/authz/user-roles"


async def test_custom_role_crud_removed(admin_client):
    r = await admin_client.post("/api/v1/config/roles", json={"code": "x", "label": "X"})
    assert r.status_code == 405


# ── Finding 1: identity 4xx must propagate, not fall back ──────────────────

async def test_identity_401_propagates_to_caller(admin_client, mocker):
    """GET /role-permissions must return 401 when identity returns 401 (not 500, not fallback)."""
    fake_response = httpx.Response(401, json={"detail": "Unauthorized"})
    mocker.patch.object(
        ac, "_fetch_matrix",
        mocker.AsyncMock(side_effect=httpx.HTTPStatusError(
            "401", request=httpx.Request("GET", "http://identity/authz/matrix"),
            response=fake_response,
        )),
    )
    ac.invalidate_cache()
    r = await admin_client.get("/api/v1/config/role-permissions")
    assert r.status_code == 401


# ── Finding 6: cache-contract tests ────────────────────────────────────────

async def test_cache_warm_calls_fetch_once(admin_client, mocker):
    """Two GETs within TTL must call _fetch_matrix exactly once."""
    fetch = mocker.patch.object(ac, "_fetch_matrix", mocker.AsyncMock(return_value=FAKE_MATRIX))
    ac.invalidate_cache()
    r1 = await admin_client.get("/api/v1/config/role-permissions")
    r2 = await admin_client.get("/api/v1/config/role-permissions")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert fetch.await_count == 1, f"Expected 1 call to _fetch_matrix, got {fetch.await_count}"


async def test_patch_invalidates_cache(admin_client, mocker):
    """Successful PATCH must invalidate the cache so the next GET re-fetches."""
    # Warm the cache
    mocker.patch.object(ac, "_fetch_matrix", mocker.AsyncMock(return_value=FAKE_MATRIX))
    ac.invalidate_cache()
    await admin_client.get("/api/v1/config/role-permissions")

    # PATCH succeeds → cache should be invalidated
    mocker.patch.object(ac, "forward", mocker.AsyncMock(return_value=(200, FAKE_MATRIX)))
    await admin_client.patch(
        "/api/v1/config/role-permissions", json={"auditor": {"view_pr": False}}
    )
    assert ac._cache is None, "Cache should be None after a successful PATCH"


# ── Bug fix: token=None must never hit identity on cold cache ───────────────

async def test_token_none_skips_identity_and_falls_back(mocker):
    """Cold cache + token=None must go straight to frozen JSONB, never call identity."""
    ac.invalidate_cache()
    fetch = mocker.patch.object(ac, "_fetch_matrix", mocker.AsyncMock())
    frozen = mocker.patch.object(
        ac, "_frozen_fallback", mocker.AsyncMock(return_value={"x": {}})
    )
    result = await ac.get_matrix(None, None)
    assert result == {"x": {}}
    fetch.assert_not_awaited()
    frozen.assert_awaited_once()


# ── F1: PATCH write-through to the local mirror ─────────────────────────────

async def test_patch_writes_through_to_local_mirror(admin_client, mocker, test_engine):
    """A successful PATCH must persist the same change into the local
    company_config.role_permissions mirror (not just forward to identity).
    """
    mocker.patch.object(ac, "forward", mocker.AsyncMock(return_value=(200, FAKE_MATRIX)))
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        r = await admin_client.patch(
            "/api/v1/config/role-permissions", json={"auditor": {"view_pr": False}}
        )
        assert r.status_code == 200
        async with factory() as db:
            cfg = await config_crud.get_or_create(db)
            effective = config_crud.get_effective_role_permissions(cfg)
        assert effective["auditor"]["view_pr"] is False
    finally:
        # Restore so later tests see the default matrix. Written directly
        # (not via config_crud.update_role_permissions) to avoid needing a
        # real users-table actor id for the updated_by FK.
        from sqlalchemy.orm.attributes import flag_modified
        async with factory() as db:
            cfg = await config_crud.get_or_create(db)
            stored = dict(cfg.role_permissions)
            stored["auditor"] = {**stored.get("auditor", {}), "view_pr": True}
            cfg.role_permissions = stored
            flag_modified(cfg, "role_permissions")
            await db.commit()


async def test_patch_returns_200_when_mirror_write_fails(admin_client, mocker):
    """A local mirror-write failure must not fail the request — identity already
    accepted the change, so its 200 + body must still be returned to the caller.
    """
    mocker.patch.object(ac, "forward", mocker.AsyncMock(return_value=(200, FAKE_MATRIX)))
    mocker.patch.object(
        config_crud, "update_role_permissions",
        mocker.AsyncMock(side_effect=RuntimeError("mirror db down")),
    )
    r = await admin_client.patch(
        "/api/v1/config/role-permissions", json={"auditor": {"view_pr": False}}
    )
    assert r.status_code == 200
    assert r.json() == FAKE_MATRIX


# ── F2: GET /me/permissions outage fallback ─────────────────────────────────

async def test_me_permissions_falls_back_on_request_error(finance_client, mocker):
    """A transport-level failure (httpx.RequestError) must fall back to the
    local mirror + the caller's own JWT role, returning 200 — not 502.
    """
    mocker.patch.object(
        ac, "forward", mocker.AsyncMock(side_effect=httpx.ConnectError("down")),
    )
    r = await finance_client.get("/api/v1/config/me/permissions")
    assert r.status_code == 200
    body = r.json()
    assert body["roles"] == ["finance_manager"]
    assert body["permissions"]["view_pa"] is True  # finance_manager default (locked True)


async def test_me_permissions_propagates_4xx(finance_client, mocker):
    """A real 4xx from identity must still propagate — only RequestError falls back."""
    mocker.patch.object(
        ac, "forward", mocker.AsyncMock(return_value=(401, {"detail": "Unauthorized"})),
    )
    r = await finance_client.get("/api/v1/config/me/permissions")
    assert r.status_code == 401
