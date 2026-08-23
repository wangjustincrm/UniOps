"""POST /api/v1/admin/wms-sync — configured() guard (Task 8/finding review).

wms_configured() existed (wms_sync/reader.py) but was never enforced at the
endpoint: with WMS_* left blank (docker-compose.prod.yml's documented "feature
hidden" state), the trigger would previously fall through to fetch_inventory()
and surface an opaque oracledb/DSN error as an unhandled 500 instead of a
clear 4xx, mirroring the precedent in epms-api/app/api/v1/nc_purchase_sync.py
and finance-api/app/api/v1/nc_coa_sync.py.
"""
import pytest


@pytest.mark.anyio
async def test_wms_sync_503_when_not_configured(client, admin_token, monkeypatch):
    from app.api.v1 import admin_sync as admin_sync_module

    monkeypatch.setattr(admin_sync_module, "wms_configured", lambda: False)

    resp = await client.post(
        "/api/v1/admin/wms-sync",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 503
