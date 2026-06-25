"""Test the import-from-erp users endpoint with a stubbed mdm-api client."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


def _mock_mdm_client(person: dict | None = None, supplier_calls_raise=False) -> MagicMock:
    """Build a MagicMock that mimics `MdmClient(bearer)` returning an async ctx mgr."""
    stub = AsyncMock()
    if supplier_calls_raise:
        stub.get_person.side_effect = Exception("boom")
    else:
        stub.get_person.return_value = person
    ctx = AsyncMock()
    ctx.__aenter__.return_value = stub
    ctx.__aexit__.return_value = None
    constructor = MagicMock(return_value=ctx)
    return constructor


@pytest.mark.asyncio
async def test_import_from_erp_creates_user(admin_client):
    person = {
        "erp_person_code": "100100",
        "person_name": "Ayan Asim",
        "department_code": None,
        "department_name": "Production",
        "company_code": "10024",
        "company_name": "Canada Royal Milk ULC",
        "is_valid": True,
    }
    with patch("app.api.v1.users.MdmClient", _mock_mdm_client(person=person)):
        resp = await admin_client.post(
            "/api/v1/users/import-from-erp",
            json={"items": [{"erp_person_code": "100100", "email": "ayan@royalmilk.com"}]},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["created"]) == 1
    assert data["created"][0]["email"] == "ayan@royalmilk.com"
    assert len(data["created"][0]["temp_password"]) >= 12
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_import_from_erp_records_error_when_person_missing(admin_client):
    with patch("app.api.v1.users.MdmClient", _mock_mdm_client(person=None)):
        resp = await admin_client.post(
            "/api/v1/users/import-from-erp",
            json={"items": [{"erp_person_code": "999999", "email": "x@royalmilk.com"}]},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["created"] == []
    assert data["errors"][0]["erp_person_code"] == "999999"
    assert "not found" in data["errors"][0]["reason"].lower()


@pytest.mark.asyncio
async def test_import_from_erp_rejects_empty_email(admin_client):
    resp = await admin_client.post(
        "/api/v1/users/import-from-erp",
        json={"items": [{"erp_person_code": "100100", "email": ""}]},
    )
    assert resp.status_code == 422
