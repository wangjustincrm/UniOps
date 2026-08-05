"""POST/GET forecast versions + grid cell upsert (Task 1).

Adapted from the task brief's TDD test skeleton: the brief's snippet calls
`client.post(...)` with no Authorization header, but this service's real
`client` fixture (tests/conftest.py) requires a bearer token — HTTPBearer
403s with none present. Every call below carries `admin_token` (system_admin
bypasses the permission-matrix lookup entirely, see
packages/authz/uniops_authz/core.py bind()), matching the pattern already
used by test_inventory_endpoint.py / test_admin_sync_endpoint.py.
"""
import pytest


@pytest.mark.anyio
async def test_create_version_and_upsert_cells(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    v = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": "2026-09", "note": "初版"},
        headers=headers,
    )).json()
    assert v["horizon_months"] == 18 and v["status"] == "draft"

    r = await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [
            {"material_code": "S0093", "month": "2026-09", "qty": "20000"},
            {"material_code": "S0093", "month": "2026-10", "qty": "18000"},
        ]},
        headers=headers,
    )
    assert r.status_code == 200 and r.json()["upserted"] == 2

    grid = (await client.get(
        f"/api/v1/forecast/versions/{v['id']}/grid", headers=headers
    )).json()
    assert len(grid["months"]) == 18 and grid["months"][0] == "2026-09"
    row = next(x for x in grid["rows"] if x["material_code"] == "S0093")
    assert float(row["total"]) == 38000
    assert float(grid["column_totals"]["2026-09"]) == 20000


@pytest.mark.anyio
async def test_frozen_cells_are_not_overwritten(client, db_session, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    from app.models.forecast import ForecastLine

    v = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": "2026-09"},
        headers=headers,
    )).json()
    await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [{"material_code": "S0060", "month": "2026-09", "qty": "100"}]},
        headers=headers,
    )

    # 冻结该格
    import sqlalchemy as sa
    await db_session.execute(sa.update(ForecastLine).where(
        ForecastLine.material_code == "S0060").values(freeze_flag=True))
    await db_session.commit()

    r = await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [{"material_code": "S0060", "month": "2026-09", "qty": "999"}]},
        headers=headers,
    )
    body = r.json()
    assert body["upserted"] == 0
    assert {"material_code": "S0060", "month": "2026-09"} in body["skipped_frozen"]


@pytest.mark.anyio
async def test_list_versions_paginated(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    for i in range(3):
        await client.post(
            "/api/v1/forecast/versions",
            json={"horizon_start_month": "2026-09"},
            headers=headers,
        )
    r = await client.get("/api/v1/forecast/versions", params={"page": 1, "page_size": 2}, headers=headers)
    body = r.json()
    assert r.status_code == 200
    assert body["total"] == 3
    assert body["page"] == 1 and body["page_size"] == 2
    assert len(body["items"]) == 2


@pytest.mark.anyio
async def test_confirm_supersedes_previous_confirmed(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    v1 = (await client.post(
        "/api/v1/forecast/versions", json={"horizon_start_month": "2026-09"}, headers=headers
    )).json()
    r1 = await client.post(f"/api/v1/forecast/versions/{v1['id']}/confirm", headers=headers)
    assert r1.status_code == 200 and r1.json()["status"] == "confirmed"

    v2 = (await client.post(
        "/api/v1/forecast/versions", json={"horizon_start_month": "2026-10"}, headers=headers
    )).json()
    r2 = await client.post(f"/api/v1/forecast/versions/{v2['id']}/confirm", headers=headers)
    assert r2.status_code == 200 and r2.json()["status"] == "confirmed"

    listing = (await client.get("/api/v1/forecast/versions", params={"page_size": 50}, headers=headers)).json()
    statuses = {item["id"]: item["status"] for item in listing["items"]}
    assert statuses[v1["id"]] == "superseded"
    assert statuses[v2["id"]] == "confirmed"


@pytest.mark.anyio
async def test_confirmed_version_rejects_cell_writes(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    v = (await client.post(
        "/api/v1/forecast/versions", json={"horizon_start_month": "2026-09"}, headers=headers
    )).json()
    await client.post(f"/api/v1/forecast/versions/{v['id']}/confirm", headers=headers)

    r = await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [{"material_code": "S0093", "month": "2026-09", "qty": "1"}]},
        headers=headers,
    )
    assert r.status_code == 409


@pytest.mark.anyio
async def test_copy_from_version_id_drops_out_of_horizon_months(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    src = (await client.post(
        "/api/v1/forecast/versions", json={"horizon_start_month": "2026-01"}, headers=headers
    )).json()
    # Source horizon = 2026-01..2027-06 (18 months from Jan 2026); both cells
    # below are valid *source* months.
    await client.put(
        f"/api/v1/forecast/versions/{src['id']}/cells",
        json={"cells": [
            {"material_code": "S0093", "month": "2026-01", "qty": "5"},
            {"material_code": "S0060", "month": "2027-06", "qty": "9"},
        ]},
        headers=headers,
    )

    # New version starting 2028-01 (horizon 2028-01..2029-06) has zero
    # overlap with the source's 2026-01..2027-06 — as-is copy (no
    # month-shifting) must drop both source lines entirely.
    dst = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": "2028-01", "copy_from_version_id": src["id"]},
        headers=headers,
    )).json()
    grid = (await client.get(f"/api/v1/forecast/versions/{dst['id']}/grid", headers=headers)).json()
    assert grid["rows"] == []

    # Sanity: a destination horizon that DOES overlap the source carries the
    # overlapping month forward as-is.
    dst2 = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": "2026-01", "copy_from_version_id": src["id"]},
        headers=headers,
    )).json()
    grid2 = (await client.get(f"/api/v1/forecast/versions/{dst2['id']}/grid", headers=headers)).json()
    codes = {r["material_code"] for r in grid2["rows"]}
    assert codes == {"S0093", "S0060"}


@pytest.mark.anyio
async def test_horizon_months_generated_server_side_not_from_client(client, admin_token):
    """Client-supplied month lists must never be trusted — only
    horizon_start_month (+ horizon_months count) drives the grid."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    v = (await client.post(
        "/api/v1/forecast/versions",
        json={"horizon_start_month": "2026-11", "horizon_months": 3},
        headers=headers,
    )).json()
    grid = (await client.get(f"/api/v1/forecast/versions/{v['id']}/grid", headers=headers)).json()
    assert grid["months"] == ["2026-11", "2026-12", "2027-01"]
