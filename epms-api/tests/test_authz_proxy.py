"""epms authz proxy: pass-through, cache, and fallback when identity is down."""
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
