"""Monthly net-requirement rollup (Task 4).

Pure-function cases come straight from the task brief; the DB-facing cases
below exercise `get_opening_stock_breakdown`'s two exclusion rules (WMS
hold/expired lots don't count; only a consignment lot's LATEST count_date
counts) plus the end-to-end `GET /api/v1/net-requirement` endpoint.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.net_requirement import compute_net_requirements, get_opening_stock_breakdown


# ── Pure function: compute_net_requirements ─────────────────────────────────


def test_stock_is_consumed_before_generating_net_requirement():
    rows = compute_net_requirements(
        {"2026-09": Decimal("100"), "2026-10": Decimal("100"), "2026-11": Decimal("100")},
        opening_stock=Decimal("250"))
    assert [r.net_requirement for r in rows] == [Decimal("0"), Decimal("0"), Decimal("50")]
    assert [r.closing_stock for r in rows] == [Decimal("150"), Decimal("50"), Decimal("0")]


def test_zero_stock_passes_forecast_through():
    rows = compute_net_requirements({"2026-09": Decimal("80")}, opening_stock=Decimal("0"))
    assert rows[0].net_requirement == Decimal("80")


def test_unsorted_month_input_still_processed_chronologically():
    # Insertion order is deliberately reversed/scrambled — the function must
    # sort months itself, never trust dict iteration order.
    rows = compute_net_requirements(
        {"2026-11": Decimal("100"), "2026-09": Decimal("100"), "2026-10": Decimal("100")},
        opening_stock=Decimal("250"))
    assert [r.month for r in rows] == ["2026-09", "2026-10", "2026-11"]
    assert [r.net_requirement for r in rows] == [Decimal("0"), Decimal("0"), Decimal("50")]
    assert [r.closing_stock for r in rows] == [Decimal("150"), Decimal("50"), Decimal("0")]


def test_opening_stock_of_month_n_is_closing_stock_of_month_n_minus_1():
    rows = compute_net_requirements(
        {"2026-09": Decimal("100"), "2026-10": Decimal("100")}, opening_stock=Decimal("250"))
    assert rows[0].opening_stock == Decimal("250")
    assert rows[1].opening_stock == rows[0].closing_stock == Decimal("150")


# ── DB-facing: get_opening_stock_breakdown ──────────────────────────────────


@pytest.mark.anyio
async def test_expired_and_hold_wms_lots_are_excluded(db_session):
    from app.models.wms_inventory import WmsInventoryLot

    today = date(2026, 8, 4)
    db_session.add_all([
        # counts: available, not expired
        WmsInventoryLot(
            warehouse_id="CANADA", material_code="S0093", lot_no="LOT-OK",
            qty=Decimal("100"), qty_allocated=Decimal("0"), qty_onhold=Decimal("10"),
            mapped_status="available", expiry_date=today + timedelta(days=30),
            sync_batch_id="b1",
        ),
        # excluded: mapped_status='hold'
        WmsInventoryLot(
            warehouse_id="CANADA", material_code="S0093", lot_no="LOT-HOLD",
            qty=Decimal("500"), qty_allocated=Decimal("0"), qty_onhold=Decimal("0"),
            mapped_status="hold", expiry_date=today + timedelta(days=30),
            sync_batch_id="b1",
        ),
        # excluded: expired (expiry_date < today) even though mapped_status='available'
        WmsInventoryLot(
            warehouse_id="CANADA", material_code="S0093", lot_no="LOT-EXPIRED",
            qty=Decimal("300"), qty_allocated=Decimal("0"), qty_onhold=Decimal("0"),
            mapped_status="available", expiry_date=today - timedelta(days=1),
            sync_batch_id="b1",
        ),
        # counts: available with no expiry_date at all (NULL is not "expired")
        WmsInventoryLot(
            warehouse_id="CANADA", material_code="S0093", lot_no="LOT-NO-EXPIRY",
            qty=Decimal("50"), qty_allocated=Decimal("0"), qty_onhold=Decimal("0"),
            mapped_status="available", expiry_date=None,
            sync_batch_id="b1",
        ),
    ])
    await db_session.commit()

    breakdown = await get_opening_stock_breakdown(db_session, "S0093", today=today)
    # (100 - 10) + (50 - 0) = 140; hold and expired lots contribute nothing
    assert breakdown.wms_qty == Decimal("140")


@pytest.mark.anyio
async def test_consignment_only_latest_count_per_lot_is_summed(db_session):
    from app.models.consignment import ConsignmentStock

    db_session.add_all([
        # same lot counted three different weeks -- only the latest counts
        ConsignmentStock(
            warehouse_code="MAIN", material_code="S0093", lot_no="LOT-A",
            qty=Decimal("100"), count_date=date(2026, 7, 21),
        ),
        ConsignmentStock(
            warehouse_code="MAIN", material_code="S0093", lot_no="LOT-A",
            qty=Decimal("80"), count_date=date(2026, 7, 28),
        ),
        ConsignmentStock(
            warehouse_code="MAIN", material_code="S0093", lot_no="LOT-A",
            qty=Decimal("60"), count_date=date(2026, 8, 4),
        ),
        # a different lot, counted once
        ConsignmentStock(
            warehouse_code="MAIN", material_code="S0093", lot_no="LOT-B",
            qty=Decimal("25"), count_date=date(2026, 8, 4),
        ),
    ])
    await db_session.commit()

    breakdown = await get_opening_stock_breakdown(db_session, "S0093", today=date(2026, 8, 4))
    # only LOT-A's 2026-08-04 count (60) plus LOT-B's 25 -- never 100+80+60+25
    assert breakdown.consignment_qty == Decimal("85")
    assert breakdown.consignment_count_date == date(2026, 8, 4)


@pytest.mark.anyio
async def test_consignment_lot_that_stops_being_counted_does_not_contribute(db_session):
    """I3 (final-phase review): a stock count is a full SNAPSHOT of a
    warehouse's consignment stock, not an append-only per-lot ledger.
    LOT-A is counted 500 in week 1; week 2's count only re-counts a
    DIFFERENT lot (LOT-A shipped out and simply wasn't re-entered). LOT-A's
    500 must NOT still contribute after week 2, even though its week-1 row
    is still sitting in the table — summing each lot at its own latest
    count_date (the pre-fix behavior) would keep counting it forever."""
    from app.models.consignment import ConsignmentStock

    db_session.add_all([
        ConsignmentStock(
            warehouse_code="MAIN", material_code="S0093", lot_no="LOT-A",
            qty=Decimal("500"), count_date=date(2026, 7, 21),
        ),
        ConsignmentStock(
            warehouse_code="MAIN", material_code="S0093", lot_no="LOT-B",
            qty=Decimal("40"), count_date=date(2026, 7, 28),
        ),
    ])
    await db_session.commit()

    breakdown = await get_opening_stock_breakdown(db_session, "S0093", today=date(2026, 8, 4))
    # latest snapshot for (MAIN, S0093) is 2026-07-28 -- only LOT-B's 40
    # counts; LOT-A's 500 from the superseded 2026-07-21 snapshot must not.
    assert breakdown.consignment_qty == Decimal("40")
    assert breakdown.consignment_count_date == date(2026, 7, 28)


@pytest.mark.anyio
async def test_consignment_each_warehouse_gets_its_own_latest_snapshot(db_session):
    """The "latest count_date" that gates which rows count is scoped per
    (warehouse_code, material_code) — a warehouse that hasn't been counted
    as recently as another still contributes its own latest snapshot, not
    zero and not forced onto some other warehouse's latest date."""
    from app.models.consignment import ConsignmentStock

    db_session.add_all([
        # MAIN's latest snapshot: 2026-08-04
        ConsignmentStock(
            warehouse_code="MAIN", material_code="S0093", lot_no="LOT-A",
            qty=Decimal("60"), count_date=date(2026, 8, 4),
        ),
        # SECOND's latest snapshot is older (2026-07-21) but still counts
        ConsignmentStock(
            warehouse_code="SECOND", material_code="S0093", lot_no="LOT-C",
            qty=Decimal("15"), count_date=date(2026, 7, 21),
        ),
    ])
    await db_session.commit()

    breakdown = await get_opening_stock_breakdown(db_session, "S0093", today=date(2026, 8, 4))
    assert breakdown.consignment_qty == Decimal("75")


@pytest.mark.anyio
async def test_opening_stock_is_zero_when_no_inventory_rows_exist(db_session):
    breakdown = await get_opening_stock_breakdown(db_session, "S9999-NOTHING-HERE", today=date(2026, 8, 4))
    assert breakdown.wms_qty == Decimal("0")
    assert breakdown.consignment_qty == Decimal("0")
    assert breakdown.consignment_count_date is None
    assert breakdown.opening_stock == Decimal("0")


# ── Endpoint: GET /api/v1/net-requirement ───────────────────────────────────


@pytest.mark.anyio
async def test_net_requirement_endpoint_end_to_end(client, db_session, admin_token):
    from app.models.wms_inventory import WmsInventoryLot

    headers = {"Authorization": f"Bearer {admin_token}"}

    v = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": "2026-09", "horizon_months": 3},
        headers=headers,
    )).json()
    await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [
            {"material_code": "S0093", "month": "2026-09", "qty": "100"},
            {"material_code": "S0093", "month": "2026-10", "qty": "100"},
            {"material_code": "S0093", "month": "2026-11", "qty": "100"},
        ]},
        headers=headers,
    )

    db_session.add(WmsInventoryLot(
        warehouse_id="CANADA", material_code="S0093", lot_no="LOT-OK",
        qty=Decimal("250"), qty_allocated=Decimal("0"), qty_onhold=Decimal("0"),
        mapped_status="available", expiry_date=date(2027, 1, 1),
        sync_batch_id="b1",
    ))
    await db_session.commit()

    r = await client.get(
        "/api/v1/net-requirement",
        params={"version_id": v["id"], "material_code": "S0093"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1 and len(body["items"]) == 1
    item = body["items"][0]
    assert item["material_code"] == "S0093"
    assert [m["month"] for m in item["months"]] == ["2026-09", "2026-10", "2026-11"]
    assert [Decimal(m["net_requirement"]) for m in item["months"]] == [Decimal("0"), Decimal("0"), Decimal("50")]
    assert [Decimal(m["closing_stock"]) for m in item["months"]] == [Decimal("150"), Decimal("50"), Decimal("0")]
    assert Decimal(item["stock_sources"]["wms_qty"]) == Decimal("250")
    assert Decimal(item["stock_sources"]["consignment_qty"]) == Decimal("0")
    assert item["stock_sources"]["consignment_count_date"] is None


@pytest.mark.anyio
async def test_net_requirement_material_code_omitted_returns_all_materials(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    v = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": "2026-09", "horizon_months": 2},
        headers=headers,
    )).json()
    await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [
            {"material_code": "S0093", "month": "2026-09", "qty": "10"},
            {"material_code": "S0060", "month": "2026-09", "qty": "20"},
        ]},
        headers=headers,
    )

    r = await client.get(
        "/api/v1/net-requirement",
        params={"version_id": v["id"]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    codes = {item["material_code"] for item in body["items"]}
    assert codes == {"S0093", "S0060"}


@pytest.mark.anyio
async def test_net_requirement_populates_material_name_from_mdm(client, admin_token, monkeypatch):
    """Same "bare code, no name" gap the forecast grid had — see this
    module's docstring."""
    import app.api.v1.net_requirement as net_requirement_module
    monkeypatch.setattr(
        net_requirement_module, "resolve_material_names",
        lambda token: _net_req_async({"S0093": "Whole Milk Powder 25kg"}),
    )

    headers = {"Authorization": f"Bearer {admin_token}"}
    v = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": "2026-09", "horizon_months": 1},
        headers=headers,
    )).json()
    await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [{"material_code": "S0093", "month": "2026-09", "qty": "10"}]},
        headers=headers,
    )

    r = await client.get(
        "/api/v1/net-requirement",
        params={"version_id": v["id"], "material_code": "S0093"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["name"] == "Whole Milk Powder 25kg"


@pytest.mark.anyio
async def test_net_requirement_degrades_to_null_name_when_mdm_api_unreachable(client, admin_token, monkeypatch):
    """mdm-api being down must not break this endpoint — degrade `name` to
    null and still return 200, same contract as the forecast grid. Patches
    at the mdm_client module level so this exercises the real degrade path."""
    import httpx as _httpx
    from app.services import mdm_client

    def _boom(token):
        raise _httpx.ConnectError("connection refused")

    monkeypatch.setattr(mdm_client, "fetch_materials", _boom)

    headers = {"Authorization": f"Bearer {admin_token}"}
    v = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": "2026-09", "horizon_months": 1},
        headers=headers,
    )).json()
    await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [{"material_code": "S0093", "month": "2026-09", "qty": "10"}]},
        headers=headers,
    )

    r = await client.get(
        "/api/v1/net-requirement",
        params={"version_id": v["id"], "material_code": "S0093"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["name"] is None


async def _net_req_async(value):
    return value


@pytest.mark.anyio
async def test_net_requirement_unknown_material_code_404s(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    v = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": "2026-09", "horizon_months": 1},
        headers=headers,
    )).json()
    r = await client.get(
        "/api/v1/net-requirement",
        params={"version_id": v["id"], "material_code": "NOT-IN-VERSION"},
        headers=headers,
    )
    assert r.status_code == 404
