"""Sales-forecast Excel template/import/export (Phase 1A Task 2).

Adapted from the task brief's TDD skeleton: the brief's snippet calls
`client.post(...)` with no Authorization header, but this service's real
`client` fixture requires a bearer token (see tests/test_forecast.py) —
every call below carries `admin_token`. `fetch_valid_material_codes` is
monkeypatched on the `forecast_io` module directly (not imported by name
into forecast.py) so the patch takes effect at the call site.
"""
import io

import httpx
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
    # `row` must be the physical Excel row number (header = row 1) so users
    # can locate the bad row in their spreadsheet: data row 1 (S0093/20,000)
    # is Excel row 2, so BOGUS (2nd data row) is row 3 and abc (3rd) is row 4.
    assert any(e["row"] == 3 for e in body["error_rows"])  # BOGUS row
    assert any(e["row"] == 4 for e in body["error_rows"])  # abc row
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


@pytest.mark.anyio
async def test_import_returns_503_when_mdm_api_unreachable(client, admin_token, monkeypatch):
    """mdm-api being down must fail loudly and clearly — never silently fall
    back to an empty valid-codes set, which would mark every material code
    as unknown and mislead the user into thinking their data is wrong."""
    from app.services import forecast_io

    def _boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", _boom)

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    buf = _xlsx([["S0093", "x", "5"] + [""] * 17], months)
    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=true",
        files={"file": ("f.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "mdm-api" in detail or "unavailable" in detail
    assert "retry" in detail.lower()


@pytest.mark.anyio
async def test_import_rejects_non_xlsx_extension(client, admin_token, monkeypatch):
    """I7 (final-phase review): a .csv (or any non-.xlsx) upload must be a
    readable 400, not fall through to openpyxl and raise BadZipFile as an
    opaque 500."""
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093"})

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=true",
        files={"file": ("f.csv", io.BytesIO(b"Material Code,Name\n"), "text/csv")},
        headers=headers,
    )
    assert r.status_code == 400
    assert ".xlsx" in r.json()["detail"]


@pytest.mark.anyio
async def test_import_rejects_disallowed_content_type(client, admin_token, monkeypatch):
    """Extension is .xlsx but Content-Type is something else entirely — the
    content-type check must fire independently of the extension check."""
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093"})

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    buf = _xlsx([["S0093", "x", "5"] + [""] * 17], months)
    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=true",
        files={"file": ("f.xlsx", buf, "text/plain")},
        headers=headers,
    )
    assert r.status_code == 400
    assert "content type" in r.json()["detail"]


@pytest.mark.anyio
async def test_import_rejects_oversized_file(client, admin_token, monkeypatch):
    """A file over the size cap must 400 before ever being parsed — the cap
    is turned down to a few bytes here so an ordinary test-sized xlsx trips
    it without needing to actually build a 10 MB file."""
    import app.api.v1.forecast as forecast_module
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093"})
    monkeypatch.setattr(forecast_module, "_MAX_IMPORT_FILE_BYTES", 10)

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    buf = _xlsx([["S0093", "x", "5"] + [""] * 17], months)
    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=true",
        files={"file": ("f.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert r.status_code == 400
    assert "too large" in r.json()["detail"]


@pytest.mark.anyio
async def test_import_rejects_corrupt_xlsx_as_400_not_500(client, admin_token, monkeypatch):
    """A file that passes the extension/content-type/size checks but isn't
    actually a readable xlsx (truncated upload, wrong bytes entirely, ...)
    must still come back as a 400 with a readable message — previously
    load_workbook's BadZipFile/InvalidFileException was uncaught and
    surfaced as an opaque 500."""
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093"})

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=true",
        files={"file": ("f.xlsx", io.BytesIO(b"this is not a zip/xlsx file at all"),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "could not read" in detail


@pytest.mark.anyio
async def test_import_rejects_file_over_the_row_cap(client, admin_token, monkeypatch):
    """More data rows than MAX_IMPORT_DATA_ROWS must 400 with a clear
    "split it up" message rather than silently truncating the import or
    parsing an unbounded number of rows."""
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093"})
    monkeypatch.setattr(forecast_io, "MAX_IMPORT_DATA_ROWS", 2)

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)

    buf = _xlsx([["S0093", "x", "5"] + [""] * 17 for _ in range(3)], months)
    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=true",
        files={"file": ("f.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert r.status_code == 400
    assert "more than 2 data rows" in r.json()["detail"]


@pytest.mark.anyio
async def test_export_then_reimport_round_trips_clean(client, admin_token, monkeypatch):
    """Template/export/import must agree on shape: exporting a version with
    data and feeding that file straight back into import must validate
    clean and reproduce the same cell values."""
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes", lambda *a, **k: {"S0093", "S0060"})

    headers = {"Authorization": f"Bearer {admin_token}"}
    v, months = await _make_version(client, headers)
    await client.put(
        f"/api/v1/forecast/versions/{v['id']}/cells",
        json={"cells": [
            {"material_code": "S0093", "month": months[0], "qty": "20000"},
            {"material_code": "S0093", "month": months[1], "qty": "18500.5"},
            {"material_code": "S0060", "month": months[0], "qty": "100"},
        ]},
        headers=headers,
    )
    original_grid = (await client.get(f"/api/v1/forecast/versions/{v['id']}/grid", headers=headers)).json()

    exported = await client.get(f"/api/v1/forecast/versions/{v['id']}/export", headers=headers)
    assert exported.status_code == 200

    r = await client.post(
        f"/api/v1/forecast/versions/{v['id']}/import?dry_run=false",
        files={"file": ("roundtrip.xlsx", io.BytesIO(exported.content),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error_rows"] == []
    assert body["skipped_frozen"] == []

    replayed_grid = (await client.get(f"/api/v1/forecast/versions/{v['id']}/grid", headers=headers)).json()
    assert replayed_grid == original_grid
