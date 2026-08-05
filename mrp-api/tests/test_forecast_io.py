"""Sales-forecast Excel template/import/export (Phase 1A Task 2).

Adapted from the task brief's TDD skeleton: the brief's snippet calls
`client.post(...)` with no Authorization header, but this service's real
`client` fixture requires a bearer token (see tests/test_forecast.py) —
every call below carries `admin_token`. `fetch_valid_material_codes` is
monkeypatched on the `forecast_io` module directly (not imported by name
into forecast.py) so the patch takes effect at the call site.
"""
import io

import pytest
from openpyxl import Workbook, load_workbook


def _xlsx(rows, months):
    wb = Workbook()
    ws = wb.active
    ws.append(["Material Code", "Name"] + months)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


async def _make_version(client, headers, start_month="2026-09"):
    v = (await client.post(
        "/api/v1/forecast/versions", json={"horizon_start_month": start_month}, headers=headers,
    )).json()
    months = (await client.get(
        f"/api/v1/forecast/versions/{v['id']}/grid", headers=headers,
    )).json()["months"]
    return v, months


@pytest.mark.anyio
async def test_import_dry_run_reports_errors_without_writing(client, admin_token, monkeypatch):
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093"})

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    buf = _xlsx([["S0093", "x", "20,000"] + [""] * 17,
                 ["BOGUS", "y", "5"] + [""] * 17,
                 ["S0093", "x", "abc"] + [""] * 17], months)
    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=true",
        files={"file": ("f.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok_rows"] == 1  # thousands separator parsed correctly
    assert len(body["error_rows"]) == 2  # unknown material + non-numeric qty
    assert any(e["row"] == 2 for e in body["error_rows"])  # BOGUS row
    assert any(e["row"] == 3 for e in body["error_rows"])  # abc row
    assert body["skipped_frozen"] == []
    assert body["would_upsert"] == 1

    grid = (await client.get(f"/api/v1/forecast/versions/{v['id']}/grid", headers=headers)).json()
    assert grid["grand_total"] in (0, "0", "0.000")  # dry_run must not write


@pytest.mark.anyio
async def test_import_applies_when_not_dry_run(client, admin_token, monkeypatch):
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093"})

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    buf = _xlsx([["S0093", "x", "20,000"] + [""] * 17], months)
    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=false",
        files={"file": ("f.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    body = r.json()
    assert body["ok_rows"] == 1
    assert body["would_upsert"] == 1
    assert body["error_rows"] == []

    grid = (await client.get(f"/api/v1/forecast/versions/{v['id']}/grid", headers=headers)).json()
    row = next(x for x in grid["rows"] if x["material_code"] == "S0093")
    assert float(row["cells"][months[0]]) == 20000


@pytest.mark.anyio
async def test_import_skips_frozen_cells_and_reports_them(client, db_session, admin_token, monkeypatch):
    from app.services import forecast_io
    from app.models.forecast import ForecastLine
    import sqlalchemy as sa

    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093"})

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [{"material_code": "S0093", "month": months[0], "qty": "100"}]},
        headers=headers,
    )
    await db_session.execute(sa.update(ForecastLine).where(
        ForecastLine.material_code == "S0093").values(freeze_flag=True))
    await db_session.commit()

    buf = _xlsx([["S0093", "x", "999"] + [""] * 17], months)
    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=false",
        files={"file": ("f.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    body = r.json()
    assert body["ok_rows"] == 1
    assert body["would_upsert"] == 0
    assert {"material_code": "S0093", "month": months[0]} in body["skipped_frozen"]

    grid = (await client.get(f"/api/v1/forecast/versions/{v['id']}/grid", headers=headers)).json()
    row = next(x for x in grid["rows"] if x["material_code"] == "S0093")
    assert float(row["cells"][months[0]]) == 100  # untouched by the import


@pytest.mark.anyio
async def test_import_rejects_mismatched_month_header(client, admin_token, monkeypatch):
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093"})

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    bad_months = ["9999-99"] + months[1:]  # first column header wrong
    buf = _xlsx([["S0093", "x", "5"] + [""] * 17], bad_months)
    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=true",
        files={"file": ("f.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    body = r.json()
    assert any(e["row"] == 0 for e in body["error_rows"])  # header-level error
    assert body["ok_rows"] == 0  # the mismatched column's cells are never read


@pytest.mark.anyio
async def test_import_rejects_negative_quantity(client, admin_token, monkeypatch):
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093"})

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    buf = _xlsx([["S0093", "x", "-5"] + [""] * 17], months)
    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=true",
        files={"file": ("f.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    body = r.json()
    assert body["ok_rows"] == 0
    assert len(body["error_rows"]) == 1


@pytest.mark.anyio
async def test_import_tolerates_dollar_sign(client, admin_token, monkeypatch):
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093"})

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    buf = _xlsx([["S0093", "x", "$1,234.50"] + [""] * 17], months)
    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=true",
        files={"file": ("f.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    body = r.json()
    assert body["ok_rows"] == 1
    assert body["error_rows"] == []


@pytest.mark.anyio
async def test_import_into_non_draft_version_is_409(client, admin_token, monkeypatch):
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093"})

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)
    await client.post(f"/api/v1/forecast/versions/{v['id']}/confirm", headers=headers)

    buf = _xlsx([["S0093", "x", "5"] + [""] * 17], months)
    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=true",
        files={"file": ("f.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert r.status_code == 409


@pytest.mark.anyio
async def test_template_download_has_material_name_and_18_month_headers(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    r = await client.get(f"/api/v1/forecast/versions/{v['id']}/template", headers=headers)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert v["version_no"] in r.headers["content-disposition"]

    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    header = [c.value for c in next(ws.iter_rows(max_row=1))]
    assert header == ["Material Code", "Name"] + months
    assert ws.max_row == 1  # empty template — no data rows


@pytest.mark.anyio
async def test_export_reflects_current_grid(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)
    await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [{"material_code": "S0093", "month": months[0], "qty": "42"}]},
        headers=headers,
    )

    r = await client.get(f"/api/v1/forecast/versions/{v['id']}/export", headers=headers)
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    header = [c.value for c in next(ws.iter_rows(max_row=1))]
    assert header == ["Material Code", "Name"] + months

    data_rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(data_rows) == 1
    assert data_rows[0][0] == "S0093"
    assert float(data_rows[0][2]) == 42
