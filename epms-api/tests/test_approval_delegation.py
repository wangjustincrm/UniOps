"""Tests for EPMS → Approval Engine delegation error mapping.

These tests mock the HTTP layer so they run without a live approval-api.
They verify that EPMS correctly translates approval engine responses into
appropriate HTTP status codes for the frontend.
"""
import pytest
from unittest.mock import AsyncMock, patch

URL = "/api/v1/pr"

_PR_PAYLOAD = {
    "title": "Test delegation PR",
    "type": 2,
    "currency": "CAD",
    "line_items": [
        {
            "description": "Widget",
            "qty": "1",
            "unit": "EA",
            "unit_price": "100.00",
        }
    ],
}


async def _create_and_submit(client) -> dict:
    r = await client.post(URL, json=_PR_PAYLOAD)
    assert r.status_code == 201, r.text
    pr = r.json()
    # Submit so the PR is in a state that can be actioned
    with patch("app.services.approval_client.delegate_action", new_callable=AsyncMock) as mock_delegate:
        mock_delegate.return_value = {"status": "submitted", "message": "OK"}
        r = await client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})
        assert r.status_code == 200, r.text
    return pr


@pytest.mark.asyncio
async def test_delegation_success_maps_to_200(admin_client):
    """Successful approval-engine response → 200 OK."""
    pr = await _create_and_submit(admin_client)
    with patch("app.services.approval_client.delegate_action", new_callable=AsyncMock) as mock_delegate:
        mock_delegate.return_value = {"status": "in_review", "message": "OK"}
        r = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "approve"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_delegation_conflict_maps_to_409(admin_client):
    """Approval engine 409 (state conflict) → EPMS returns 409, not 502."""
    pr = await _create_and_submit(admin_client)
    with patch("app.services.approval_client.delegate_action", new_callable=AsyncMock) as mock_delegate:
        mock_delegate.side_effect = ValueError("Invalid action for current status")
        r = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "approve"})
    assert r.status_code == 409
    assert "Invalid action" in r.json()["detail"]


@pytest.mark.asyncio
async def test_delegation_not_found_maps_to_404(admin_client):
    """Approval engine 404 (document not found) → EPMS returns 404."""
    pr = await _create_and_submit(admin_client)
    with patch("app.services.approval_client.delegate_action", new_callable=AsyncMock) as mock_delegate:
        mock_delegate.side_effect = LookupError("PR not found in engine")
        r = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "approve"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delegation_engine_down_maps_to_502(admin_client):
    """Approval engine unavailable → EPMS returns 502 Bad Gateway."""
    pr = await _create_and_submit(admin_client)
    with patch("app.services.approval_client.delegate_action", new_callable=AsyncMock) as mock_delegate:
        mock_delegate.side_effect = RuntimeError("Approval Engine error 503: Service Unavailable")
        r = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "approve"})
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_action_on_nonexistent_pr_returns_404(admin_client):
    """EPMS 404 check happens before delegation — no engine call for missing PRs."""
    import uuid
    fake_id = str(uuid.uuid4())
    with patch("app.services.approval_client.delegate_action", new_callable=AsyncMock) as mock_delegate:
        r = await admin_client.post(f"{URL}/{fake_id}/action", json={"action": "submit"})
    assert r.status_code == 404
    mock_delegate.assert_not_called()
