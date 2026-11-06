"""HTTP surface: sync status/trigger, settings, seeds, and permission gates."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.nc_sync import scheduler


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["service"] == "inventory-api"


async def test_status_before_any_sync(client, auth_headers):
    r = await client.get("/api/v1/admin/nc-sync/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] is None and body["last_success_at"] is None
    assert body["interval_minutes"] == 60


async def test_manual_sync_without_nc_config_is_503(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.v1.admin_sync.nc_configured", lambda: False)
    r = await client.post("/api/v1/admin/nc-sync", headers=auth_headers)
    assert r.status_code == 503


async def test_seeded_warehouse_classes_and_unclassified_flag(client, auth_headers, db_session):
    from sqlalchemy import insert

    from app.models import NcWarehouse
    # NC has SC11 (seeded as wms) and SC99 (nobody classified it yet).
    await db_session.execute(insert(NcWarehouse), [
        {"pk_stordoc": "W11", "code": "SC11", "name": "Raw", "iscalculatedinvcost": True},
        {"pk_stordoc": "W99", "code": "SC99", "name": "New", "iscalculatedinvcost": True},
    ])
    await db_session.commit()

    r = await client.get("/api/v1/settings/warehouse-classes", headers=auth_headers)
    assert r.status_code == 200
    items = {i["code"]: i for i in r.json()["items"]}
    assert items["SC11"]["warehouse_class"] == "wms"
    assert items["SC16"]["warehouse_class"] == "wms"
    assert items["SC08"]["warehouse_class"] == "external"
    assert items["Pro01"]["warehouse_class"] == "production"
    assert items["021"]["warehouse_class"] == "transient"
    assert items["SC99"]["warehouse_class"] == "unclassified"
    assert r.json()["unclassified_count"] == 1


async def test_put_warehouse_class_validates(client, auth_headers):
    bad = await client.put("/api/v1/settings/warehouse-classes/SC99", headers=auth_headers,
                           json={"warehouse_class": "warehouse"})
    assert bad.status_code == 422
    ok = await client.put("/api/v1/settings/warehouse-classes/SC99", headers=auth_headers,
                          json={"warehouse_class": "external", "note": "3PL"})
    assert ok.status_code == 200


async def test_no_batch_materials_seeded_and_editable(client, auth_headers):
    r = await client.get("/api/v1/settings/no-batch-materials", headers=auth_headers)
    assert {m["material_code"]: m["since_date"] for m in r.json()} == {
        "CF00AF": "2026-01-01", "CF00WT": "2026-01-01"}
    r = await client.put("/api/v1/settings/no-batch-materials/cf0099", headers=auth_headers,
                         json={"since_date": "2026-05-01"})
    assert r.status_code == 200 and r.json()["material_code"] == "CF0099"
    assert (await client.delete("/api/v1/settings/no-batch-materials/CF0099", headers=auth_headers)).status_code == 204


@pytest.mark.parametrize("key,value,ok", [
    ("nc_time_zone", "America/Toronto", True),
    ("nc_time_zone", "UTC", False),
    ("nc_sync_interval_minutes", 0, True),
    ("nc_sync_interval_minutes", -5, False),
    ("nc_sync_interval_minutes", True, False),
    ("qty_tolerance", 0.5, True),
    ("no_such_setting", 1, False),
])
async def test_params_validation(client, auth_headers, key, value, ok):
    r = await client.put(f"/api/v1/settings/params/{key}", headers=auth_headers, json={"value": value})
    assert (r.status_code == 200) is ok, r.text
    if ok:
        got = (await client.get("/api/v1/settings/params", headers=auth_headers)).json()["values"]
        assert got[key] == value


def test_is_due():
    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    assert scheduler.is_due(last_attempt_at=None, interval_minutes=60, now=now)
    assert not scheduler.is_due(last_attempt_at=None, interval_minutes=0, now=now)
    assert not scheduler.is_due(last_attempt_at=now - timedelta(minutes=59), interval_minutes=60, now=now)
    assert scheduler.is_due(last_attempt_at=now - timedelta(minutes=60), interval_minutes=60, now=now)
    assert not scheduler.is_due(last_attempt_at=now + timedelta(minutes=5), interval_minutes=60, now=now)


async def test_wms_history_note(client, auth_headers):
    r = (await client.get("/api/v1/settings/wms-history", headers=auth_headers)).json()
    assert r["history_start"] == "2025-02-03" and r["go_live_month"] == "2025-01"
