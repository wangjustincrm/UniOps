"""GET forecast versions + grid/export (Task 1). Write/confirm/import
endpoints (`POST /versions`, `PUT .../cells`, `POST .../confirm`,
`POST .../import`) were retired in the Continuous Sales Forecast redesign
(Task 8) -- editing now happens against the living series (see
tests/test_demand_series.py for `upsert_cells`'s own suite and
tests/test_outlook.py for `freeze_outlook`, the only way a version is
created today).

Every test below builds its fixture `ForecastVersion`/`ForecastLine` rows
directly against the ORM (`db_session`) instead of going through those
retired endpoints, then exercises only the read/export surface this module
still exposes.
"""
import uuid
from decimal import Decimal

import pytest

from app.models.forecast import ForecastLine, ForecastVersion


async def _make_version(db_session, horizon_start_month: str, horizon_months: int = 18) -> ForecastVersion:
    version = ForecastVersion(
        version_no=f"FCV-{horizon_start_month}-{uuid.uuid4().hex[:8].upper()}",
        status="draft",
        horizon_start_month=horizon_start_month,
        horizon_months=horizon_months,
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(version)
    return version


async def _make_line(db_session, version_id, material_code: str, month: str, qty: Decimal) -> None:
    db_session.add(ForecastLine(version_id=version_id, material_code=material_code, month=month, qty=qty))
    await db_session.commit()


@pytest.mark.anyio
async def test_list_versions_paginated(client, db_session, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    for _ in range(3):
        await _make_version(db_session, "2026-09")
    r = await client.get("/api/v1/forecast/versions", params={"page": 1, "page_size": 2}, headers=headers)
    body = r.json()
    assert r.status_code == 200
    assert body["total"] == 3
    assert body["page"] == 1 and body["page_size"] == 2
    assert len(body["items"]) == 2


@pytest.mark.anyio
async def test_grid_populates_material_names_from_mdm(client, db_session, admin_token, monkeypatch):
    """The gap this fix closes: `name` used to be hardcoded null for every
    row (see this module's old docstring) — planners saw bare codes like
    S0093 with no product description."""
    import app.api.v1.forecast as forecast_module
    monkeypatch.setattr(
        forecast_module, "resolve_material_names",
        lambda token: _async_result({"S0093": "Whole Milk Powder 25kg"}),
    )

    headers = {"Authorization": f"Bearer {admin_token}"}
    v = await _make_version(db_session, "2026-09")
    await _make_line(db_session, v.id, "S0093", "2026-09", Decimal("1"))

    grid = (await client.get(f"/api/v1/forecast/versions/{v.id}/grid", headers=headers)).json()
    row = next(x for x in grid["rows"] if x["material_code"] == "S0093")
    assert row["name"] == "Whole Milk Powder 25kg"


@pytest.mark.anyio
async def test_grid_degrades_to_null_names_when_mdm_api_unreachable(client, db_session, admin_token, monkeypatch):
    """mdm-api being down must not break the grid — the forecast page is the
    primary screen; a name-lookup failure must degrade every row's `name`
    to null (today's pre-fix behavior) and the request must still be a 200,
    never a 5xx and never hang. Patches at the mdm_client module level (not
    the imported reference) so this exercises the real degrade path inside
    resolve_material_names/_fetch_materials_safe, not a test-side bypass."""
    import httpx as _httpx
    from app.services import mdm_client

    def _boom(token):
        raise _httpx.ConnectError("connection refused")

    monkeypatch.setattr(mdm_client, "fetch_materials", _boom)

    headers = {"Authorization": f"Bearer {admin_token}"}
    v = await _make_version(db_session, "2026-09")
    await _make_line(db_session, v.id, "S0093", "2026-09", Decimal("1"))

    r = await client.get(f"/api/v1/forecast/versions/{v.id}/grid", headers=headers)
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["material_code"] == "S0093")
    assert row["name"] is None


@pytest.mark.anyio
async def test_grid_resolves_names_in_one_batched_call_not_per_row(client, db_session, admin_token, monkeypatch):
    """Multiple rows in the same grid must trigger exactly one mdm-api round
    trip, not one per row."""
    import app.api.v1.forecast as forecast_module
    calls: list[str] = []

    def _spy(token):
        calls.append(token)
        return _async_result({"S0093": "Whole Milk Powder", "S0060": "Skim Milk Powder"})

    monkeypatch.setattr(forecast_module, "resolve_material_names", _spy)

    headers = {"Authorization": f"Bearer {admin_token}"}
    v = await _make_version(db_session, "2026-09")
    await _make_line(db_session, v.id, "S0093", "2026-09", Decimal("1"))
    await _make_line(db_session, v.id, "S0060", "2026-09", Decimal("1"))

    grid = (await client.get(f"/api/v1/forecast/versions/{v.id}/grid", headers=headers)).json()
    assert len(calls) == 1  # one call for the whole grid, not one per row
    names = {r["material_code"]: r["name"] for r in grid["rows"]}
    assert names == {"S0093": "Whole Milk Powder", "S0060": "Skim Milk Powder"}


@pytest.mark.anyio
async def test_grid_skips_mdm_call_entirely_when_there_are_no_rows(client, db_session, admin_token, monkeypatch):
    """An empty version (no forecast lines) has nothing to name — the grid
    must not make a needless mdm-api round trip for zero rows."""
    import app.api.v1.forecast as forecast_module
    calls: list[str] = []

    def _spy(token):
        calls.append(token)
        return _async_result({})

    monkeypatch.setattr(forecast_module, "resolve_material_names", _spy)

    headers = {"Authorization": f"Bearer {admin_token}"}
    v = await _make_version(db_session, "2026-09")
    grid = (await client.get(f"/api/v1/forecast/versions/{v.id}/grid", headers=headers)).json()
    assert grid["rows"] == []
    assert calls == []


@pytest.mark.anyio
async def test_export_also_populates_material_names(client, db_session, admin_token, monkeypatch):
    """The Excel export already carries a Name column (build_export_workbook
    writes TEMPLATE_HEADERS = ['Material Code', 'Name']) — it should be as
    populated as the grid, not the odd one out."""
    import io
    from openpyxl import load_workbook
    import app.api.v1.forecast as forecast_module
    monkeypatch.setattr(
        forecast_module, "resolve_material_names",
        lambda token: _async_result({"S0093": "Whole Milk Powder 25kg"}),
    )

    headers = {"Authorization": f"Bearer {admin_token}"}
    v = await _make_version(db_session, "2026-09")
    await _make_line(db_session, v.id, "S0093", "2026-09", Decimal("1"))

    r = await client.get(f"/api/v1/forecast/versions/{v.id}/export", headers=headers)
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    data_row = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
    assert data_row[0] == "S0093"
    assert data_row[1] == "Whole Milk Powder 25kg"


async def _async_result(value):
    """Helper: a monkeypatched `resolve_material_names` replacement must
    still be awaitable — the endpoint always does `await resolve_material_names(token)`."""
    return value


@pytest.mark.anyio
async def test_grid_months_generated_from_stored_horizon(client, db_session, admin_token):
    """`_generate_months()` is the single source of a version's YYYY-MM
    column list — always derived fresh from horizon_start_month/
    horizon_months, never trusted from any request (there is no request
    input to trust anymore: a version's horizon is set once, either by
    freeze_outlook or a direct ORM write, and is immutable thereafter)."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    v = await _make_version(db_session, "2026-11", horizon_months=3)
    grid = (await client.get(f"/api/v1/forecast/versions/{v.id}/grid", headers=headers)).json()
    assert grid["months"] == ["2026-11", "2026-12", "2027-01"]
