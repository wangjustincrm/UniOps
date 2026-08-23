"""uom_conversions — mirrored from NC ERP unitTranf (MRP phase0 task 3)."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models.erp_sync_state import ErpSyncState


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


@pytest.mark.anyio
async def test_uom_conversion_sync_advances_cursor_to_max_rowversion(client, db_session, monkeypatch):
    """unitTranf rows carry `rowversion` just like material/supplier/person
    (主数据与MES数据集成接口文档v1.1.txt §2.8, e.g. line 239). The sync
    cursor (ErpSyncState.last_ts) must advance to the max rowversion of the
    synced batch, matching sync_kind's pattern — not fall back to wall-clock
    now()."""
    from app.services import erp_sync

    async def fake_fetch(*a, **k):
        return [
            {"unit_CODE": "KGM", "unit_TYPE": "KGM", "unit_RATE": "1.00000000",
             "rowversion": "2019-08-13 07:58:36"},
            {"unit_CODE": "GRM", "unit_TYPE": "KGM", "unit_RATE": "0.00100000",
             "rowversion": "2020-01-01 00:00:00"},
        ]

    monkeypatch.setattr(erp_sync, "fetch_unit_tranf", fake_fetch, raising=False)

    resp = await client.post("/mdm/v1/uom-conversions/sync")
    assert resp.status_code == 200

    state = await db_session.get(ErpSyncState, "uom_conversion")
    assert state is not None
    assert state.last_ts == datetime(2020, 1, 1, tzinfo=timezone.utc)
