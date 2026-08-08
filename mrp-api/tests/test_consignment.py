"""Consignment stock entry + WMS lot expiry lookup (Task 3).

`lookup_lot` is monkeypatched on the `consignment` API module (never the
`wms_lot_lookup` module directly, and never real WMS) — same idiom
test_wms_sync_service.py uses for `service.fetch_inventory`, matching how
consignment.py imports the bare name specifically so this works.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.api.v1 import consignment


@pytest.mark.anyio
async def test_create_auto_fills_expiry_from_wms(client, admin_token, monkeypatch):
    monkeypatch.setattr(
        consignment, "lookup_lot",
        lambda lot_no, material_code: {
            "found": True,
            "production_date": date(2026, 1, 3),
            "expiry_date": date(2027, 1, 2),
        },
    )
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/v1/consignment/stock",
        json={
            "material_code": "S0093", "lot_no": "HGC1976532",
            "qty": "1200", "count_date": "2026-08-04",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expiry_date"] == "2027-01-02"
    assert body["expiry_source"] == "wms"
    assert body["wms_lookup_found"] is True
    assert body["warehouse_code"] == "MAIN"


@pytest.mark.anyio
async def test_create_wms_down_does_not_block_save(client, admin_token, monkeypatch):
    def _boom(lot_no, material_code):
        raise RuntimeError("this should never be reached — lookup_lot itself never raises")

    # Simulate what lookup_lot itself returns when WMS is unreachable
    # (per its own contract: it swallows the failure, never raises).
    monkeypatch.setattr(
        consignment, "lookup_lot",
        lambda lot_no, material_code: {"found": False, "production_date": None, "expiry_date": None},
    )
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/v1/consignment/stock",
        json={
            "material_code": "S0093", "lot_no": "UNREACHABLE-LOT",
            "qty": "500", "count_date": "2026-08-04",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expiry_date"] is None
    assert body["expiry_source"] is None
    assert body["wms_lookup_found"] is False


@pytest.mark.anyio
async def test_create_lot_not_found_does_not_block_save(client, admin_token, monkeypatch):
    monkeypatch.setattr(
        consignment, "lookup_lot",
        lambda lot_no, material_code: {"found": False, "production_date": None, "expiry_date": None},
    )
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/v1/consignment/stock",
        json={
            "material_code": "S0060", "lot_no": "NEVER-SEEN-BY-WMS",
            "qty": "300", "count_date": "2026-08-04",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expiry_date"] is None
    assert body["expiry_source"] is None
    assert body["wms_lookup_found"] is False


@pytest.mark.anyio
async def test_create_manual_expiry_marks_source_manual(client, admin_token, monkeypatch):
    called = False

    def _should_not_be_called(lot_no, material_code):
        nonlocal called
        called = True
        return {"found": True, "production_date": None, "expiry_date": date(2099, 1, 1)}

    monkeypatch.setattr(consignment, "lookup_lot", _should_not_be_called)
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/v1/consignment/stock",
        json={
            "material_code": "S0093", "lot_no": "HGC1976532",
            "qty": "1200", "count_date": "2026-08-04",
            "expiry_date": "2026-12-31",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expiry_date"] == "2026-12-31"
    assert body["expiry_source"] == "manual"
    assert body["wms_lookup_found"] is None
    assert called is False, "lookup_lot must not be called when the caller supplies expiry_date"


@pytest.mark.anyio
async def test_duplicate_wh_material_lot_date_is_409_not_500(client, admin_token, monkeypatch):
    monkeypatch.setattr(
        consignment, "lookup_lot",
        lambda lot_no, material_code: {"found": False, "production_date": None, "expiry_date": None},
    )
    headers = {"Authorization": f"Bearer {admin_token}"}
    payload = {
        "material_code": "S0093", "lot_no": "HGC1976532",
        "qty": "1200", "count_date": "2026-08-04",
    }
    r1 = await client.post("/api/v1/consignment/stock", json=payload, headers=headers)
    assert r1.status_code == 201, r1.text

    r2 = await client.post("/api/v1/consignment/stock", json=payload, headers=headers)
    assert r2.status_code == 409, r2.text

    # The failed duplicate must not have poisoned the session/transaction —
    # a normal read afterwards should still work.
    r3 = await client.get("/api/v1/consignment/stock", headers=headers)
    assert r3.status_code == 200
    assert r3.json()["total"] == 1


@pytest.mark.anyio
async def test_list_reports_latest_count_date_per_warehouse(client, admin_token, monkeypatch):
    monkeypatch.setattr(
        consignment, "lookup_lot",
        lambda lot_no, material_code: {"found": False, "production_date": None, "expiry_date": None},
    )
    headers = {"Authorization": f"Bearer {admin_token}"}
    await client.post(
        "/api/v1/consignment/stock",
        json={"material_code": "S0093", "lot_no": "LOT-A", "qty": "100", "count_date": "2026-07-28"},
        headers=headers,
    )
    await client.post(
        "/api/v1/consignment/stock",
        json={"material_code": "S0093", "lot_no": "LOT-B", "qty": "100", "count_date": "2026-08-04"},
        headers=headers,
    )

    r = await client.get("/api/v1/consignment/stock", headers=headers)
    body = r.json()
    assert body["total"] == 2
    assert body["latest_count_dates"]["MAIN"] == "2026-08-04"


@pytest.mark.anyio
async def test_list_populates_material_names_from_mdm(client, admin_token, monkeypatch):
    """The same "bare code, no name" gap the forecast grid had — see
    app/api/v1/consignment.py's module docstring."""
    monkeypatch.setattr(
        consignment, "lookup_lot",
        lambda lot_no, material_code: {"found": False, "production_date": None, "expiry_date": None},
    )
    monkeypatch.setattr(
        consignment, "resolve_material_names",
        lambda token: _consignment_async({"S0093": "Whole Milk Powder 25kg"}),
    )
    headers = {"Authorization": f"Bearer {admin_token}"}
    await client.post(
        "/api/v1/consignment/stock",
        json={"material_code": "S0093", "lot_no": "LOT-A", "qty": "100", "count_date": "2026-08-04"},
        headers=headers,
    )

    r = await client.get("/api/v1/consignment/stock", headers=headers)
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["material_code"] == "S0093"
    assert item["name"] == "Whole Milk Powder 25kg"


@pytest.mark.anyio
async def test_list_degrades_to_null_names_when_mdm_api_unreachable(client, admin_token, monkeypatch):
    """mdm-api being down must not break this list — degrade every row's
    `name` to null and still return 200, same contract as the forecast grid
    (app/services/mdm_client.py's resolve_material_names). Patches at the
    mdm_client module level so this exercises the real degrade path."""
    import httpx as _httpx
    from app.services import mdm_client

    def _boom(token):
        raise _httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        consignment, "lookup_lot",
        lambda lot_no, material_code: {"found": False, "production_date": None, "expiry_date": None},
    )
    monkeypatch.setattr(mdm_client, "fetch_materials", _boom)

    headers = {"Authorization": f"Bearer {admin_token}"}
    await client.post(
        "/api/v1/consignment/stock",
        json={"material_code": "S0093", "lot_no": "LOT-A", "qty": "100", "count_date": "2026-08-04"},
        headers=headers,
    )

    r = await client.get("/api/v1/consignment/stock", headers=headers)
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["name"] is None


async def _consignment_async(value):
    return value


@pytest.mark.anyio
async def test_lot_lookup_endpoint_delegates_to_service(client, admin_token, monkeypatch):
    monkeypatch.setattr(
        consignment, "lookup_lot",
        lambda lot_no, material_code: {
            "found": True,
            "production_date": date(2025, 1, 3),
            "expiry_date": date(2027, 1, 2),
        },
    )
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.get(
        "/api/v1/consignment/lot-lookup",
        params={"lot_no": "HGC1976532", "material_code": "CF0086"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"found": True, "production_date": "2025-01-03", "expiry_date": "2027-01-02"}


@pytest.mark.anyio
async def test_lot_history_endpoint_delegates_to_service(client, admin_token, monkeypatch):
    monkeypatch.setattr(
        consignment, "list_lots",
        lambda material_code: [
            {"lot_no": "LOT-A", "production_date": date(2026, 5, 20), "expiry_date": date(2028, 5, 19)},
            {"lot_no": "LOT-B", "production_date": None, "expiry_date": None},
        ],
    )
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.get(
        "/api/v1/consignment/lot-history",
        params={"material_code": "S0093"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json() == {"items": [
        {"lot_no": "LOT-A", "production_date": "2026-05-20", "expiry_date": "2028-05-19"},
        {"lot_no": "LOT-B", "production_date": None, "expiry_date": None},
    ]}


@pytest.mark.anyio
async def test_patch_qty_and_delete(client, admin_token, monkeypatch):
    monkeypatch.setattr(
        consignment, "lookup_lot",
        lambda lot_no, material_code: {"found": False, "production_date": None, "expiry_date": None},
    )
    headers = {"Authorization": f"Bearer {admin_token}"}
    created = (await client.post(
        "/api/v1/consignment/stock",
        json={"material_code": "S0093", "lot_no": "LOT-PATCH", "qty": "100", "count_date": "2026-08-04"},
        headers=headers,
    )).json()

    r = await client.patch(
        f"/api/v1/consignment/stock/{created['id']}",
        json={"qty": "250", "expiry_date": "2026-12-01"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert Decimal(body["qty"]) == Decimal("250")
    assert body["expiry_date"] == "2026-12-01"
    assert body["expiry_source"] == "manual"

    d = await client.delete(f"/api/v1/consignment/stock/{created['id']}", headers=headers)
    assert d.status_code == 204

    g = await client.get(f"/api/v1/consignment/stock/{created['id']}", headers=headers)
    assert g.status_code == 404
