"""M12 (final-phase review): every other test in this suite authenticates
via `admin_token` (role=system_admin), which short-circuits
`require_permission(...)` before it ever looks at a permission key (see
uniops_authz.core's system_admin fast path in app/core/authz.py). That
means a gated endpoint whose permission key was misspelled, or swapped for
the wrong one entirely, would still pass all 61 tests — nothing exercises
the actual key lookup.

This file uses the `non_admin_token` fixture (tests/conftest.py) — a
role="requester" JWT that does NOT short-circuit — to prove each gated
endpoint group actually 403s a caller who lacks the key. mrp-api's own
alembic chain doesn't own identity's role_permissions/role_defs/user_roles
tables (this service's `mrp_test` database never has them, same situation
mdm-api's test_boms_read_authz.py documents), so `_deny_everything` below
monkeypatches `uniops_authz.core.user_role_codes`/`_effective_matrix`
directly rather than relying on real matrix rows.

One test per gated endpoint group (forecast read, demand write (series.py),
consignment read+write, inventory read, admin_sync write, net_requirement
read) — that covers every distinct permission key this service defines
(mrp.report.view, mrp.demand.write, mrp.param.write) at least once.
"""
import uuid

import pytest


def _deny_everything(monkeypatch):
    """Every role's effective permission set is empty, so any
    require_permission(key) call other than the system_admin short-circuit
    falls through to 403."""
    import uniops_authz.core as authz_core

    async def _user_role_codes(db, user_id, base_role):
        return {base_role}

    async def _effective_matrix(db):
        return {}

    monkeypatch.setattr(authz_core, "user_role_codes", _user_role_codes)
    monkeypatch.setattr(authz_core, "_effective_matrix", _effective_matrix)


@pytest.mark.anyio
async def test_forecast_read_gate_403s_non_permitted_role(client, non_admin_token, monkeypatch):
    _deny_everything(monkeypatch)
    headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.get("/api/v1/forecast/versions", headers=headers)
    assert r.status_code == 403


@pytest.mark.anyio
async def test_demand_write_gate_403s_non_permitted_role(client, non_admin_token, monkeypatch):
    """mrp.demand.write — forecast.py's own write endpoints (POST
    /versions, PUT .../cells, POST .../confirm, POST .../import) were
    retired in the Continuous Sales Forecast redesign (Task 8); this key is
    now gated on series.py's PUT /series/cells instead (also covered by
    tests/test_demand_series.py's own test_put_cells_without_write_permission_returns_403,
    and POST /series/outlook by tests/test_outlook.py's
    test_post_outlook_without_write_permission_returns_403 — kept here too
    so this file's "one test per gated group" catalogue stays complete)."""
    _deny_everything(monkeypatch)
    headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.put(
        "/api/v1/series/cells",
        json={"cells": [{"material_code": "S0093", "month": "2026-09", "qty": "1"}]},
        headers=headers,
    )
    assert r.status_code == 403


@pytest.mark.anyio
async def test_delete_forecast_version_gate_403s_non_permitted_role(client, non_admin_token, monkeypatch):
    """DELETE /forecast/versions/{id} is the one WRITE in forecast.py, and it
    is gated on mrp.demand.write rather than the read key every other
    endpoint in that module uses -- exactly the kind of per-endpoint key this
    file exists to pin. Reached before the 404, so no fixture row is needed."""
    import uuid

    _deny_everything(monkeypatch)
    headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.delete(f"/api/v1/forecast/versions/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 403


@pytest.mark.anyio
async def test_consignment_read_gate_403s_non_permitted_role(client, non_admin_token, monkeypatch):
    _deny_everything(monkeypatch)
    headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.get("/api/v1/consignment/stock", headers=headers)
    assert r.status_code == 403


@pytest.mark.anyio
async def test_consignment_write_gate_403s_non_permitted_role(client, non_admin_token, monkeypatch):
    _deny_everything(monkeypatch)
    headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.post(
        "/api/v1/consignment/stock",
        json={
            "material_code": "S0093", "lot_no": "LOT-X",
            "qty": "10", "count_date": "2026-08-04",
        },
        headers=headers,
    )
    assert r.status_code == 403


@pytest.mark.anyio
async def test_inventory_read_gate_403s_non_permitted_role(client, non_admin_token, monkeypatch):
    _deny_everything(monkeypatch)
    headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.get("/api/v1/inventory/lots", headers=headers)
    assert r.status_code == 403


@pytest.mark.anyio
async def test_admin_sync_write_gate_403s_non_permitted_role(client, non_admin_token, monkeypatch):
    """mrp.param.write — the one permission key none of the other gated
    groups use, so this is the only test in the suite that exercises it."""
    _deny_everything(monkeypatch)
    headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.post("/api/v1/admin/wms-sync", headers=headers)
    assert r.status_code == 403


@pytest.mark.anyio
async def test_net_requirement_read_gate_403s_non_permitted_role(client, non_admin_token, monkeypatch):
    _deny_everything(monkeypatch)
    headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.get(
        "/api/v1/net-requirement", params={"version_id": str(uuid.uuid4())}, headers=headers,
    )
    assert r.status_code == 403


@pytest.mark.anyio
async def test_intent_product_write_gate_403s_non_permitted_role(client, non_admin_token, monkeypatch):
    _deny_everything(monkeypatch)
    headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.post(
        "/api/v1/intent-products", json={"name": "Should Not Be Created"}, headers=headers,
    )
    assert r.status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize("path", [
    "/api/v1/inventory/aging",
    "/api/v1/inventory/materials",
    "/api/v1/inventory/materials/CR0025/open-po-lines",
    "/api/v1/inventory/batches",
    "/api/v1/inventory/batches/locations?material_code=CR0025",
])
async def test_inventory_read_gate_403s_non_permitted_role(
    client, non_admin_token, monkeypatch, path,
):
    """The Inventory endpoints added 2026-08-17. /inventory/lots is covered
    above; these three are separate route handlers, and a gate is only proven
    on the handler it decorates -- one of them silently ungated would expose
    the whole stock and on-order picture to any authenticated user.
    """
    _deny_everything(monkeypatch)
    headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.get(path, headers=headers)
    assert r.status_code == 403
