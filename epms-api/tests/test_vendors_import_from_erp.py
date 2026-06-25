"""import-from-erp vendors endpoint — writes forward to mdm (faked in conftest).

The shared FakeMdmClient (conftest) stands in for mdm-api: get_supplier reads
from FakeMdmClient.suppliers; create_partner writes the shared table.
"""
import pytest

from tests.conftest import FakeMdmClient


@pytest.mark.asyncio
async def test_import_vendor_from_erp_creates_vendor(admin_client):
    FakeMdmClient.suppliers = {"CRM027": {
        "erp_supplier_code": "CRM027",
        "supplier_name": "JiangSu Debang Duoling",
        "supplier_address": "Lianyungang, Jiangsu",
        "supplier_tel": "+86-xxx",
        "supplier_fax": None,
        "supplier_type": "YL",
    }}
    resp = await admin_client.post(
        "/api/v1/vendors/import-from-erp",
        json={
            "erp_supplier_codes": ["CRM027"],
            "defaults": {"category": "ingredients", "payment_terms": "net60", "currency": "USD"},
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["created"] == 1
    assert data["errors"] == []

    list_resp = await admin_client.get("/api/v1/vendors?search=CRM027")
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert any(v["erp_id"] == "CRM027" for v in items)


@pytest.mark.asyncio
async def test_import_vendor_skips_duplicate(admin_client):
    FakeMdmClient.suppliers = {"CRM027": {"erp_supplier_code": "CRM027", "supplier_name": "X"}}
    await admin_client.post("/api/v1/vendors/import-from-erp",
                            json={"erp_supplier_codes": ["CRM027"]})
    resp = await admin_client.post("/api/v1/vendors/import-from-erp",
                                   json={"erp_supplier_codes": ["CRM027"]})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["created"] == 0
    assert len(data["errors"]) == 1
    assert "exists" in data["errors"][0]["reason"].lower() or "already" in data["errors"][0]["reason"].lower()
