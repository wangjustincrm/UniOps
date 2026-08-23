"""epms /config/approval-delegations proxy — Task 12.

approval-api's /delegations endpoints have no browser subdomain/CORS
(same reasoning as test_approval_routing_proxy.py), so epms-api gateways
them for the Portal admin page. Mirrors that file's shape and mocks
app.services.approval_client.forward — no real approval-api call.
"""
import uuid

import httpx
import pytest

import app.services.approval_client as apc

pytestmark = pytest.mark.asyncio

_DELEGATION_ID = str(uuid.uuid4())

FAKE_DELEGATION = {
    "id": _DELEGATION_ID,
    "delegator_user_id": str(uuid.uuid4()),
    "delegate_user_id": str(uuid.uuid4()),
    "delegator_name": "Alice",
    "delegate_name": "Bob",
    "start_date": "2026-08-20",
    "end_date": "2026-09-03",
    "note": None,
    "revoked_at": None,
    "revoked_by": None,
    "created_by": str(uuid.uuid4()),
    "created_at": "2026-08-19T00:00:00Z",
    "updated_at": "2026-08-19T00:00:00Z",
}


# ── GET /config/approval-delegations ────────────────────────────────────────

async def test_list_delegations_proxies(admin_client, mocker):
    fwd = mocker.patch.object(
        apc, "forward", mocker.AsyncMock(return_value=(200, [FAKE_DELEGATION])))
    r = await admin_client.get("/api/v1/config/approval-delegations")
    assert r.status_code == 200
    assert r.json() == [FAKE_DELEGATION]
    assert fwd.await_args.args[0] == "GET"
    assert fwd.await_args.args[1] == "/delegations"


async def test_list_delegations_passes_caller_token(admin_client, mocker):
    fwd = mocker.patch.object(
        apc, "forward", mocker.AsyncMock(return_value=(200, [])))
    await admin_client.get("/api/v1/config/approval-delegations")
    token_arg = fwd.await_args.args[2]
    assert token_arg is not None and isinstance(token_arg, str) and len(token_arg) > 0


async def test_list_delegations_502_on_connection_failure(admin_client, mocker):
    mocker.patch.object(
        apc, "forward", mocker.AsyncMock(side_effect=httpx.ConnectError("down")))
    r = await admin_client.get("/api/v1/config/approval-delegations")
    assert r.status_code == 502


# ── POST /config/approval-delegations ───────────────────────────────────────

async def test_create_delegation_forwards_body_and_token(admin_client, mocker):
    fwd = mocker.patch.object(
        apc, "forward", mocker.AsyncMock(return_value=(201, FAKE_DELEGATION)))
    body = {
        "delegator_user_id": FAKE_DELEGATION["delegator_user_id"],
        "delegate_user_id": FAKE_DELEGATION["delegate_user_id"],
        "start_date": "2026-08-20", "end_date": "2026-09-03",
    }
    r = await admin_client.post("/api/v1/config/approval-delegations", json=body)
    assert r.status_code == 201
    assert r.json() == FAKE_DELEGATION
    assert fwd.await_args.args[0] == "POST"
    assert fwd.await_args.args[1] == "/delegations"
    assert fwd.await_args.kwargs["json"] == body
    assert fwd.await_args.args[2] is not None


async def test_create_delegation_409_overlap_propagates(admin_client, mocker):
    """approval-api's overlap 409 must reach the browser unchanged — the
    Portal form renders it inline next to the dates, not as a toast."""
    mocker.patch.object(
        apc, "forward",
        mocker.AsyncMock(return_value=(409, {
            "detail": "This person already has a delegation covering part of "
                      "that date range. Revoke or shorten it first."})))
    r = await admin_client.post(
        "/api/v1/config/approval-delegations",
        json={"delegator_user_id": str(uuid.uuid4()), "delegate_user_id": str(uuid.uuid4()),
              "start_date": "2026-08-20", "end_date": "2026-09-03"},
    )
    assert r.status_code == 409
    assert "already has a delegation" in r.json()["detail"]


async def test_create_delegation_422_propagates(admin_client, mocker):
    mocker.patch.object(
        apc, "forward",
        mocker.AsyncMock(return_value=(422, {"detail": "A delegator cannot delegate to themself."})))
    r = await admin_client.post(
        "/api/v1/config/approval-delegations",
        json={"delegator_user_id": "x", "delegate_user_id": "x",
              "start_date": "2026-08-20", "end_date": "2026-09-03"},
    )
    assert r.status_code == 422


async def test_create_delegation_502_on_connection_failure(admin_client, mocker):
    mocker.patch.object(
        apc, "forward", mocker.AsyncMock(side_effect=httpx.ConnectError("down")))
    r = await admin_client.post(
        "/api/v1/config/approval-delegations",
        json={"delegator_user_id": str(uuid.uuid4()), "delegate_user_id": str(uuid.uuid4()),
              "start_date": "2026-08-20", "end_date": "2026-09-03"},
    )
    assert r.status_code == 502


# ── PATCH /config/approval-delegations/{id} ─────────────────────────────────

async def test_update_delegation_forwards_body_and_token(admin_client, mocker):
    fwd = mocker.patch.object(
        apc, "forward", mocker.AsyncMock(return_value=(200, FAKE_DELEGATION)))
    r = await admin_client.patch(
        f"/api/v1/config/approval-delegations/{_DELEGATION_ID}",
        json={"end_date": "2026-09-10"},
    )
    assert r.status_code == 200
    assert fwd.await_args.args[0] == "PATCH"
    assert fwd.await_args.args[1] == f"/delegations/{_DELEGATION_ID}"
    assert fwd.await_args.kwargs["json"] == {"end_date": "2026-09-10"}


async def test_update_delegation_409_revoked_propagates(admin_client, mocker):
    mocker.patch.object(
        apc, "forward",
        mocker.AsyncMock(return_value=(409, {
            "detail": "This delegation is already revoked and cannot be edited. "
                      "Create a new one instead."})))
    r = await admin_client.patch(
        f"/api/v1/config/approval-delegations/{_DELEGATION_ID}",
        json={"end_date": "2026-09-10"},
    )
    assert r.status_code == 409


# ── POST /config/approval-delegations/{id}/revoke ───────────────────────────

async def test_revoke_delegation_proxies(admin_client, mocker):
    revoked = {**FAKE_DELEGATION, "revoked_at": "2026-08-19T12:00:00Z",
              "revoked_by": str(uuid.uuid4())}
    fwd = mocker.patch.object(
        apc, "forward", mocker.AsyncMock(return_value=(200, revoked)))
    r = await admin_client.post(f"/api/v1/config/approval-delegations/{_DELEGATION_ID}/revoke")
    assert r.status_code == 200
    assert r.json()["revoked_at"] is not None
    assert fwd.await_args.args[0] == "POST"
    assert fwd.await_args.args[1] == f"/delegations/{_DELEGATION_ID}/revoke"


async def test_revoke_delegation_502_on_connection_failure(admin_client, mocker):
    mocker.patch.object(
        apc, "forward", mocker.AsyncMock(side_effect=httpx.ConnectError("down")))
    r = await admin_client.post(f"/api/v1/config/approval-delegations/{_DELEGATION_ID}/revoke")
    assert r.status_code == 502


# ── Non-admin ────────────────────────────────────────────────────────────────

async def test_non_admin_403_propagates_on_create(requester_client, mocker):
    """epms does not re-implement the system_admin gate — approval-api's own
    403 must reach the browser unchanged, same convention as routing."""
    mocker.patch.object(
        apc, "forward",
        mocker.AsyncMock(return_value=(403, {"detail": "system_admin only"})))
    r = await requester_client.post(
        "/api/v1/config/approval-delegations",
        json={"delegator_user_id": str(uuid.uuid4()), "delegate_user_id": str(uuid.uuid4()),
              "start_date": "2026-08-20", "end_date": "2026-09-03"},
    )
    assert r.status_code == 403
