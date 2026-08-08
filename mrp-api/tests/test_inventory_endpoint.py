"""GET /api/v1/inventory/lots — response shape (items/total/page/page_size,
matching mdm-api/app/api/v1/materials.py's MaterialListResponse idiom)."""
import pytest

from app.services.wms_sync import service

FAKE_ROWS = [
    {
        "warehouseid": "CANADA", "sku": "CF0086", "lotnum": "HGC1976532",
        "qty": 420, "qtyallocated": 0, "qtyonhold": 0,
        "lotatt01": "2025-01-03", "lotatt02": "2027-01-02", "lotatt03": "2025-01-20",
        "lotatt05": "20250103 291041001", "lotatt08": "02", "lotatt13": "0000131",
        "lotatt14": "CASN2502100006*189", "edittime": None,
    },
]


@pytest.mark.anyio
async def test_list_lots_echoes_page_and_page_size(client, db_session, admin_token, monkeypatch):
    monkeypatch.setattr(service, "fetch_inventory", lambda: FAKE_ROWS)
    await service.run_wms_sync(db_session)

    resp = await client.get(
        "/api/v1/inventory/lots",
        params={"page": 1, "page_size": 20},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert len(body["items"]) == 1
