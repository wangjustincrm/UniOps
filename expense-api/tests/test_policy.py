"""PRD S4-B — OA Expense Config admin panel: GET/PATCH /api/v1/policy."""
import pytest


@pytest.mark.asyncio
async def test_get_policy_defaults(admin_client):
    """Policy singleton auto-created on first access with sensible defaults."""
    resp = await admin_client.get("/api/v1/policy")
    assert resp.status_code == 200
    data = resp.json()
    assert float(data["hst_rate"]) == pytest.approx(0.13)
    assert float(data["mileage_rate_per_km"]) > 0
    assert float(data["meal_breakfast_limit"]) > 0


@pytest.mark.asyncio
async def test_update_policy_as_admin(admin_client):
    """System admin can update any policy field; change is persisted."""
    resp = await admin_client.patch(
        "/api/v1/policy",
        json={"mileage_rate_per_km": "0.75"},
    )
    assert resp.status_code == 200
    assert float(resp.json()["mileage_rate_per_km"]) == pytest.approx(0.75)

    # Verify persisted
    resp2 = await admin_client.get("/api/v1/policy")
    assert float(resp2.json()["mileage_rate_per_km"]) == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_update_policy_as_finance_manager(finance_client):
    """Finance Manager is also allowed to update policy."""
    resp = await finance_client.patch(
        "/api/v1/policy",
        json={"max_km_per_claim": 3000},
    )
    assert resp.status_code == 200
    assert resp.json()["max_km_per_claim"] == 3000


@pytest.mark.asyncio
async def test_update_policy_forbidden_for_requester(requester_client):
    """PRD S4-B — only Finance Manager / System Admin may update policy."""
    resp = await requester_client.patch(
        "/api/v1/policy",
        json={"mileage_rate_per_km": "0.50"},
    )
    assert resp.status_code == 403
