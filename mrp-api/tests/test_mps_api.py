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

Fixture forecast versions are built directly against the ORM (`db_session`)
rather than via `POST /forecast/versions` + `PUT .../cells` +
`POST .../confirm` — those write/confirm endpoints were retired in the
Continuous Sales Forecast redesign (Task 8; see tests/test_forecast.py's
module docstring). mps.py only ever reads a version's stored rows, so a
version built this way is indistinguishable to it from one freeze_outlook
would have produced.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.api.v1 import mps as mps_module
from app.models.forecast import ForecastLine, ForecastVersion


async def _no_shelf_life(token):
    """Every material's shelf life comes back unknown (None) — safe default
    for tests that don't care about pre-build behavior: unknown shelf life
    just means the engine never pre-builds (see mps_engine.py docstring),
    so plain in-month placement is still exercised."""
    return {}


async def _confirmed_version(db_session, *, start="2026-09", months=3, material="S0093", monthly_qty="100"):
    month_list = mps_module._generate_months(start, months)
    version = ForecastVersion(
        version_no=f"FCV-{start}-{uuid.uuid4().hex[:8].upper()}",
        status="confirmed",
        horizon_start_month=start,
        horizon_months=months,
        confirmed_at=datetime.now(timezone.utc),
    )
    db_session.add(version)
    await db_session.flush()  # assign version.id for the lines' FK below
    for m in month_list:
        db_session.add(ForecastLine(
            version_id=version.id, material_code=material, month=m, qty=Decimal(monthly_qty),
        ))
    await db_session.commit()
    await db_session.refresh(version)
    return {"id": str(version.id)}, month_list


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

    version, months = await _confirmed_version(db_session)
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
async def test_confirm_release_requires_permission(client, db_session, admin_token, non_admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, _ = await _confirmed_version(db_session)
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

    forecast_version = ForecastVersion(
        version_no=f"FCV-2026-09-{uuid.uuid4().hex[:8].upper()}",
        status="confirmed",
        horizon_start_month="2026-09",
        horizon_months=2,
        confirmed_at=datetime.now(timezone.utc),
    )
    db_session.add(forecast_version)
    await db_session.flush()
    db_session.add_all([
        ForecastLine(version_id=forecast_version.id, material_code="S0093", month="2026-09", qty=Decimal("100")),
        ForecastLine(version_id=forecast_version.id, material_code="S0093", month="2026-10", qty=Decimal("100")),
        ForecastLine(version_id=forecast_version.id, material_code="S0060", month="2026-09", qty=Decimal("50")),
        ForecastLine(version_id=forecast_version.id, material_code="S0060", month="2026-10", qty=Decimal("50")),
    ])
    await db_session.commit()
    version = {"id": str(forecast_version.id)}
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
async def test_generate_run_requires_confirmed_forecast_version(client, db_session, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    v = ForecastVersion(
        version_no=f"FCV-2026-09-{uuid.uuid4().hex[:8].upper()}",
        status="draft",
        horizon_start_month="2026-09",
        horizon_months=1,
    )
    db_session.add(v)
    await db_session.commit()
    await db_session.refresh(v)
    r = await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": str(v.id)}, headers=headers,
    )
    assert r.status_code == 409


@pytest.mark.anyio
async def test_get_run_includes_capacity_occupancy(client, db_session, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(db_session, months=1, monthly_qty="40")
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
async def test_patch_line_marks_manual_adjusted_and_can_lock(client, db_session, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(db_session, months=1)
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
async def test_recalculate_keeps_locked_line_fixed(client, db_session, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(db_session, months=1, monthly_qty="100")
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


@pytest.mark.anyio
async def test_confirm_release_skips_capacity_gap_lines(client, db_session, admin_token, monkeypatch):
    """A capacity_gap line is an unmet-demand exception for a human to
    resolve, not a booked production order (mps_engine.py's own docstring:
    it "does not consume any month's capacity ledger") -- it must never be
    materialized into mrp_demands, or Phase 1C's material explosion would
    over-procure raw materials for production that can't actually happen
    this cycle. It IS still persisted as an MrpMpsLine, so it stays visible
    to the planner."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(db_session, months=1, monthly_qty="100")
    # Capacity too small to ever hold 100, and shelf life is unknown for
    # every material (see _no_shelf_life) so the engine can never pre-build
    # it either -- the only possible outcome is an explicit capacity_gap.
    await _factory_rule(client, headers, max_sku_count=50, max_output_qty="50")

    run = (await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": version["id"]}, headers=headers,
    )).json()
    assert len(run["lines"]) == 1
    assert run["lines"][0]["capacity_gap"] is True

    rel = await client.post(f"/api/v1/mps/runs/{run['id']}/confirm-release", headers=headers)
    assert rel.status_code == 200, rel.text

    import sqlalchemy as sa
    from app.models.demand import MrpDemand
    from app.models.mps import MrpMpsLine

    demand_rows = (await db_session.execute(
        sa.select(MrpDemand).where(MrpDemand.source_run_id == uuid.UUID(run["id"]))
    )).scalars().all()
    assert demand_rows == []  # the gap line must never materialize into mrp_demands

    line_rows = (await db_session.execute(
        sa.select(MrpMpsLine).where(MrpMpsLine.run_id == uuid.UUID(run["id"]))
    )).scalars().all()
    assert len(line_rows) == 1
    assert line_rows[0].capacity_gap is True  # still visible as an MrpMpsLine


@pytest.mark.anyio
async def test_confirm_release_clears_prior_cycle_demand_across_forecast_versions(
    client, db_session, admin_token, monkeypatch,
):
    """Rolling-forecast cycle: v1 confirmed -> R1 released -> forecast
    revised -> v2 confirmed (a NEW version_id -- ForecastVersion is
    single-lineage; confirming v2 supersedes v1, see
    app/models/forecast.py) -> R2 released. mrp_demands must end with ONLY
    R2's rows: R1's rows must not survive forever just because they belong
    to a different, now-superseded forecast_version_id -- that would
    silently double-count demand on every rolling-forecast cycle."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}

    v1, _ = await _confirmed_version(
        db_session, start="2026-09", months=1, material="S0093", monthly_qty="100",
    )
    await _factory_rule(client, headers)
    r1 = (await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": v1["id"]}, headers=headers,
    )).json()
    rel1 = await client.post(f"/api/v1/mps/runs/{r1['id']}/confirm-release", headers=headers)
    assert rel1.status_code == 200, rel1.text

    v2, _ = await _confirmed_version(
        db_session, start="2026-10", months=1, material="S0093", monthly_qty="120",
    )
    r2 = (await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": v2["id"]}, headers=headers,
    )).json()
    rel2 = await client.post(f"/api/v1/mps/runs/{r2['id']}/confirm-release", headers=headers)
    assert rel2.status_code == 200, rel2.text

    import sqlalchemy as sa
    from app.models.demand import MrpDemand

    rows = (await db_session.execute(sa.select(MrpDemand))).scalars().all()
    assert len(rows) == 1  # R1's row was cleared, not just left orphaned under v1
    assert rows[0].source_run_id == uuid.UUID(r2["id"])
    assert rows[0].qty == Decimal("120.000")


@pytest.mark.anyio
async def test_patch_line_rejects_malformed_plan_month(client, db_session, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(db_session, months=1)
    await _factory_rule(client, headers)
    run = (await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": version["id"]}, headers=headers,
    )).json()
    line = run["lines"][0]

    r = await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{line['id']}",
        json={"plan_month": "2026-13"},
        headers=headers,
    )
    assert r.status_code == 422, r.text
