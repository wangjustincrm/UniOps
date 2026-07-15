"""epms authz proxy: pass-through, cache, and fallback when identity is down."""
import httpx
import pytest

import app.core.authz_client as ac

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
