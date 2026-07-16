"""epms /config/approval-routing proxy — Phase 3.

approval-api is server-to-server only (no browser subdomain/CORS), so
epms-api gateways it for the Portal admin page, mirroring the identity
authz-proxy pattern in test_authz_proxy.py. Kept as its own file (rather
than growing test_authz_proxy.py) because it exercises a different
downstream client (approval_client, not authz_client) with its own routing
concerns — no shared fixtures beyond the standard *_client ones.
"""
import httpx
import pytest

import app.services.approval_client as apc

pytestmark = pytest.mark.asyncio

FAKE_ROUTING = {
    "departments": [
        {
            "dept_id": "11111111-1111-1111-1111-111111111111",
            "dept_code": "FIN",
            "dept_name": "Finance",
            "gm_or_opm": "gm",
            "director_user_id": None,
            "supervisor_enabled": False,
        }
    ],
    "backups": {"gm": None, "opm": None},
}


# ── GET /config/approval-routing ────────────────────────────────────────────

async def test_get_approval_routing_proxies(admin_client, mocker):
    fwd = mocker.patch.object(
        apc, "forward", mocker.AsyncMock(return_value=(200, FAKE_ROUTING)))
    r = await admin_client.get("/api/v1/config/approval-routing")
    assert r.status_code == 200
    assert r.json() == FAKE_ROUTING
    assert fwd.await_args.args[0] == "GET"
    assert fwd.await_args.args[1] == "/routing"


async def test_get_approval_routing_passes_caller_token(admin_client, mocker):
    """The forward call must receive the caller's own bearer token (arg 2), not None."""
    fwd = mocker.patch.object(
        apc, "forward", mocker.AsyncMock(return_value=(200, FAKE_ROUTING)))
    await admin_client.get("/api/v1/config/approval-routing")
    token_arg = fwd.await_args.args[2]
    assert token_arg is not None and isinstance(token_arg, str) and len(token_arg) > 0


async def test_get_approval_routing_propagates_4xx(admin_client, mocker):
    """A real 4xx/5xx from approval-api must pass through untouched, not be swallowed."""
    mocker.patch.object(
        apc, "forward", mocker.AsyncMock(return_value=(401, {"detail": "Unauthorized"})))
    r = await admin_client.get("/api/v1/config/approval-routing")
    assert r.status_code == 401
    assert r.json()["detail"] == "Unauthorized"


async def test_get_approval_routing_502_on_connection_failure(admin_client, mocker):
    mocker.patch.object(
        apc, "forward", mocker.AsyncMock(side_effect=httpx.ConnectError("down")))
    r = await admin_client.get("/api/v1/config/approval-routing")
    assert r.status_code == 502


# ── PUT /config/approval-routing ────────────────────────────────────────────

async def test_put_approval_routing_forwards_body_and_token(admin_client, mocker):
    fwd = mocker.patch.object(
        apc, "forward", mocker.AsyncMock(return_value=(200, FAKE_ROUTING)))
    body = {"departments": FAKE_ROUTING["departments"], "backups": {"gm": None, "opm": None}}
    r = await admin_client.put("/api/v1/config/approval-routing", json=body)
    assert r.status_code == 200
    assert r.json() == FAKE_ROUTING
    assert fwd.await_args.args[0] == "PUT"
    assert fwd.await_args.args[1] == "/routing"
    assert fwd.await_args.kwargs["json"] == body
    assert fwd.await_args.args[2] is not None


async def test_put_approval_routing_403_propagates(requester_client, mocker):
    """approval-api gates PUT to system_admin itself — its 403 must reach the
    browser unchanged; epms must not re-implement or short-circuit this gate.
    """
    mocker.patch.object(
        apc, "forward",
        mocker.AsyncMock(return_value=(403, {"detail": "system_admin only"})))
    r = await requester_client.put(
        "/api/v1/config/approval-routing",
        json={"departments": [], "backups": {}},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "system_admin only"


async def test_put_approval_routing_422_propagates(admin_client, mocker):
    """approval-api owns validation (unknown dept_id, bad gm_or_opm, unknown
    backup role) — its 422 + detail must reach the browser unchanged.
    """
    mocker.patch.object(
        apc, "forward",
        mocker.AsyncMock(return_value=(422, {"detail": "Unknown or inactive dept_id: x"})))
    r = await admin_client.put(
        "/api/v1/config/approval-routing",
        json={"departments": [{"dept_id": "x", "gm_or_opm": "gm"}], "backups": {}},
    )
    assert r.status_code == 422
    assert "Unknown or inactive dept_id" in r.json()["detail"]


async def test_put_approval_routing_502_on_connection_failure(admin_client, mocker):
    mocker.patch.object(
        apc, "forward", mocker.AsyncMock(side_effect=httpx.ConnectError("down")))
    r = await admin_client.put(
        "/api/v1/config/approval-routing",
        json={"departments": [], "backups": {}},
    )
    assert r.status_code == 502
