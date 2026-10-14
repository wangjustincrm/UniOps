"""The WMS inventory endpoints serve two modules (2026-10-14): MRP, gated
mrp.report.view / mrp.param.write, and the Inventory module, gated
inventory.view / inventory.admin. Each key admits on its own; an unrelated
key does not. Both directions are asserted — a test that only checks "403"
would also pass if everybody were refused.
"""
import pytest

from app.services.wms_sync.transform import transform_lot


def _grant(monkeypatch, *keys):
    import uniops_authz.core as authz_core

    async def _user_role_codes(db, user_id, base_role):
        return {"tester"}

    async def _effective_matrix(db):
        return {"tester": set(keys)}

    monkeypatch.setattr(authz_core, "user_role_codes", _user_role_codes)
    monkeypatch.setattr(authz_core, "_effective_matrix", _effective_matrix)


READS = ["/api/v1/inventory/lots", "/api/v1/inventory/batches", "/api/v1/inventory/aging",
         "/api/v1/inventory/materials", "/api/v1/admin/wms-sync/status"]


@pytest.mark.anyio
@pytest.mark.parametrize("url", READS)
@pytest.mark.parametrize("key", ["mrp.report.view", "inventory.view"])
async def test_reads_admit_either_module_key(client, non_admin_token, monkeypatch, url, key):
    h = {"Authorization": f"Bearer {non_admin_token}"}
    _grant(monkeypatch, "mrp.demand.write")          # unrelated
    assert (await client.get(url, headers=h)).status_code == 403
    _grant(monkeypatch, key)
    assert (await client.get(url, headers=h)).status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["mrp.param.write", "inventory.admin"])
async def test_manual_wms_sync_admits_either_admin_key(client, non_admin_token, monkeypatch, key):
    monkeypatch.setattr("app.api.v1.admin_sync.wms_configured", lambda: False)
    h = {"Authorization": f"Bearer {non_admin_token}"}
    _grant(monkeypatch, "inventory.view", "mrp.report.view")
    assert (await client.post("/api/v1/admin/wms-sync", headers=h)).status_code == 403
    _grant(monkeypatch, key)
    assert (await client.post("/api/v1/admin/wms-sync", headers=h)).status_code == 503  # admitted; WMS unset


def test_transform_carries_nc_qc_batch():
    raw = {"warehouseid": "CANADA", "sku": "CP0168", "lotnum": "HGC2002718", "qty": 7920,
           "lotatt04": "20250815011E", "lotatt05": "20250815011E", "lotatt08": "02"}
    from datetime import date
    row = transform_lot(raw, {"02": "available"}, today=date(2026, 10, 14))
    assert row["qc_batch"] == "20250815011E"
