"""uom_conversions — mirrored from NC ERP unitTranf (MRP phase0 task 3)."""
from decimal import Decimal

import pytest


@pytest.mark.anyio
async def test_uom_conversion_upsert_and_list(client, db_session, monkeypatch):
    from app.services import erp_sync

    async def fake_fetch(*a, **k):
        return [{"unit_CODE": "GRM", "unit_TYPE": "KGM", "unit_RATE": "0.001"}]

    monkeypatch.setattr(erp_sync, "fetch_unit_tranf", fake_fetch, raising=False)

    resp = await client.post("/mdm/v1/uom-conversions/sync")
    assert resp.status_code == 200

    body = (await client.get("/mdm/v1/uom-conversions")).json()
    assert any(
        r["from_uom"] == "GRM" and r["to_uom"] == "KGM"
        and Decimal(str(r["rate"])) == Decimal("0.001")
        for r in body["items"]
    )


@pytest.mark.anyio
async def test_uom_conversion_sync_is_idempotent(client, db_session, monkeypatch):
    from app.services import erp_sync

    async def fake_fetch(*a, **k):
        return [{"unit_CODE": "GRM", "unit_TYPE": "KGM", "unit_RATE": "0.001"}]

    monkeypatch.setattr(erp_sync, "fetch_unit_tranf", fake_fetch, raising=False)

    resp1 = await client.post("/mdm/v1/uom-conversions/sync")
    assert resp1.json()["inserted"] == 1
    assert resp1.json()["updated"] == 0

    async def fake_fetch_updated(*a, **k):
        return [{"unit_CODE": "GRM", "unit_TYPE": "KGM", "unit_RATE": "0.0011"}]

    monkeypatch.setattr(erp_sync, "fetch_unit_tranf", fake_fetch_updated, raising=False)
    resp2 = await client.post("/mdm/v1/uom-conversions/sync")
    assert resp2.json()["inserted"] == 0
    assert resp2.json()["updated"] == 1

    body = (await client.get("/mdm/v1/uom-conversions")).json()
    rows = [r for r in body["items"] if r["from_uom"] == "GRM" and r["to_uom"] == "KGM"]
    assert len(rows) == 1
    assert Decimal(str(rows[0]["rate"])) == Decimal("0.0011")
