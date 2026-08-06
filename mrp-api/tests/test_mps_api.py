"""MPS run API (Phase 1B Task 4, design §6.5).

End-to-end coverage over `app/api/v1/mps.py`, which wires the pure
`app/services/mps_engine.py::generate_mps` to real data: a confirmed
forecast version's net requirements (via `app/services/net_requirement.py`,
same opening-stock definition the `/net-requirement` endpoint uses),
effective factory capacity rules (`app/services/capacity.py`), and mdm-api
shelf life.

`resolve_shelf_life` is imported as a bare name into `mps.py` (same idiom
`consignment.py` uses for `lookup_lot` — see that module's docstring) so it
can be monkeypatched here without ever hitting real mdm-api.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.api.v1 import mps as mps_module


async def _no_shelf_life(token):
    """Every material's shelf life comes back unknown (None) — safe default
    for tests that don't care about pre-build behavior: unknown shelf life
    just means the engine never pre-builds (see mps_engine.py docstring),
    so plain in-month placement is still exercised."""
    return {}


async def _confirmed_version(client, headers, *, start="2026-09", months=3, material="S0093", monthly_qty="100"):
    v = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": start, "horizon_months": months},
        headers=headers,
    )).json()
    month_list = mps_module._generate_months(start, months)
    await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [
            {"material_code": material, "month": m, "qty": monthly_qty} for m in month_list
        ]},
        headers=headers,
    )
    confirmed = (await client.post(
        f"/api/v1/forecast/versions/{v['id']}/confirm", headers=headers,
    )).json()
    return confirmed, month_list


async def _factory_rule(client, headers, *, max_sku_count=50, max_output_qty="1000000"):
    await client.post(
        "/api/v1/capacity/rules",
        json={
            "scope_type": "factory", "constraint_type": "max_sku_count",
            "limit_value": str(max_sku_count), "effective_from": "2026-01-01",
        },
        headers=headers,
    )
    await client.post(
        "/api/v1/capacity/rules",
        json={
            "scope_type": "factory", "constraint_type": "max_output_qty",
            "limit_value": max_output_qty, "uom": "KG", "effective_from": "2026-01-01",
        },
        headers=headers,
    )


@pytest.mark.anyio
async def test_generate_run_and_confirm_release_end_to_end(client, db_session, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}

    version, months = await _confirmed_version(client, headers)
    await _factory_rule(client, headers)

    r = await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": version["id"]}, headers=headers,
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "draft"
    assert run["run_no"].startswith("MPS-")
    assert Decimal(run["safety_margin_fraction"]) == Decimal("0.3333")
    lines = run["lines"]
    assert len(lines) == 3
    # No opening stock anywhere -> net_requirement == forecast qty each
    # month -> each line lands directly in its own demand month (ample
    # capacity, no shelf-life pre-build attempted since shelf life unknown).
    by_month = {l["demand_month"]: l for l in lines}
    for m in months:
        assert by_month[m]["plan_month"] == m
        assert Decimal(by_month[m]["qty"]) == Decimal("100")
        assert by_month[m]["material_code"] == "S0093"
        assert by_month[m]["capacity_gap"] is False

    run_id = run["id"]
    rel = await client.post(f"/api/v1/mps/runs/{run_id}/confirm-release", headers=headers)
    assert rel.status_code == 200, rel.text
    assert rel.json()["status"] == "released"

    import sqlalchemy as sa
    from app.models.demand import MrpDemand

    rows = (await db_session.execute(
        sa.select(MrpDemand).where(MrpDemand.source_run_id == uuid.UUID(run_id))
    )).scalars().all()
    assert len(rows) == 3
    for row in rows:
        assert row.demand_month == row.demand_month  # sanity
    plan_months_from_lines = {l["plan_month"] for l in lines}
    assert {row.demand_month for row in rows} == plan_months_from_lines
    for row in rows:
        matching_line = next(l for l in lines if l["material_code"] == row.material_code and l["plan_month"] == row.demand_month)
        assert Decimal(matching_line["qty"]) == row.qty


@pytest.mark.anyio
async def test_confirm_release_requires_permission(client, admin_token, non_admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, _ = await _confirmed_version(client, headers)
    await _factory_rule(client, headers)
    run = (await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": version["id"]}, headers=headers,
    )).json()

    import uniops_authz.core as authz_core

    async def _user_role_codes(db, user_id, base_role):
        return {base_role}

    async def _effective_matrix(db):
        return {}

    monkeypatch.setattr(authz_core, "user_role_codes", _user_role_codes)
    monkeypatch.setattr(authz_core, "_effective_matrix", _effective_matrix)

    denied_headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.post(f"/api/v1/mps/runs/{run['id']}/confirm-release", headers=denied_headers)
    assert r.status_code == 403


@pytest.mark.anyio
async def test_material_with_sufficient_opening_stock_produces_no_line(client, db_session, admin_token, monkeypatch):
    """A material whose opening stock already covers every month's forecast
    has zero net requirement everywhere -> it must not appear in the
    generated MPS at all (generate_mps is only ever handed positive-qty
    DemandItems)."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}

    v = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": "2026-09", "horizon_months": 2},
        headers=headers,
    )).json()
    await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [
            {"material_code": "S0093", "month": "2026-09", "qty": "100"},
            {"material_code": "S0093", "month": "2026-10", "qty": "100"},
            {"material_code": "S0060", "month": "2026-09", "qty": "50"},
            {"material_code": "S0060", "month": "2026-10", "qty": "50"},
        ]},
        headers=headers,
    )
    version = (await client.post(
        f"/api/v1/forecast/versions/{v['id']}/confirm", headers=headers,
    )).json()
    await _factory_rule(client, headers)

    from app.models.wms_inventory import WmsInventoryLot
    # S0060's opening stock (500) covers every month's forecast (50 each) --
    # never generates a net requirement.
    db_session.add(WmsInventoryLot(
        warehouse_id="CANADA", material_code="S0060", lot_no="LOT-AMPLE",
        qty=Decimal("500"), qty_allocated=Decimal("0"), qty_onhold=Decimal("0"),
        mapped_status="available", expiry_date=date(2027, 1, 1),
        sync_batch_id="b1",
    ))
    await db_session.commit()

    r = await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": version["id"]}, headers=headers,
    )
    assert r.status_code == 201, r.text
    lines = r.json()["lines"]
    materials = {l["material_code"] for l in lines}
    assert materials == {"S0093"}
    assert len(lines) == 2  # S0093's two months only -- S0060 contributes nothing


@pytest.mark.anyio
async def test_generate_run_requires_confirmed_forecast_version(client, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    v = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": "2026-09", "horizon_months": 1},
        headers=headers,
    )).json()
    r = await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": v["id"]}, headers=headers,
    )
    assert r.status_code == 409


@pytest.mark.anyio
async def test_get_run_includes_capacity_occupancy(client, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(client, headers, months=1, monthly_qty="40")
    await _factory_rule(client, headers, max_sku_count=5, max_output_qty="1000")

    run = (await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": version["id"]}, headers=headers,
    )).json()

    r = await client.get(f"/api/v1/mps/runs/{run['id']}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    occ = {o["month"]: o for o in body["capacity_occupancy"]}
    assert occ[months[0]]["used_sku_count"] == 1
    assert Decimal(occ[months[0]]["used_qty"]) == Decimal("40")
    assert occ[months[0]]["max_sku_count"] == 5
    assert Decimal(occ[months[0]]["max_output_qty"]) == Decimal("1000")


@pytest.mark.anyio
async def test_patch_line_marks_manual_adjusted_and_can_lock(client, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(client, headers, months=1)
    await _factory_rule(client, headers)

    run = (await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": version["id"]}, headers=headers,
    )).json()
    line = run["lines"][0]

    r = await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{line['id']}",
        json={"qty": "77", "locked_by_planner": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["qty"]) == Decimal("77")
    assert body["manual_adjusted"] is True
    assert body["locked_by_planner"] is True


@pytest.mark.anyio
async def test_recalculate_keeps_locked_line_fixed(client, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(client, headers, months=1, monthly_qty="100")
    await _factory_rule(client, headers)

    run = (await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": version["id"]}, headers=headers,
    )).json()
    line = run["lines"][0]

    await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{line['id']}",
        json={"qty": "999", "locked_by_planner": True},
        headers=headers,
    )

    r = await client.post(f"/api/v1/mps/runs/{run['id']}/recalculate", headers=headers)
    assert r.status_code == 200, r.text
    lines = r.json()["lines"]
    assert len(lines) == 1
    assert lines[0]["locked_by_planner"] is True
    assert Decimal(lines[0]["qty"]) == Decimal("999")  # untouched by recalculation
