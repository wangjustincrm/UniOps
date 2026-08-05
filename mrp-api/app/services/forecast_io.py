"""Sales forecast Excel template / import / export helpers (Phase 1A Task 2).

Consumes Task 1's `ForecastVersion`/`ForecastLine` shape via the caller
(app/api/v1/forecast.py) — this module only deals with xlsx bytes and
row-level validation, it does not touch the DB session itself.

`fetch_valid_material_codes` is deliberately a *synchronous* function (an
`httpx.Client`, not `AsyncClient`) that pages through mdm-api's
`GET /mdm/v1/materials` **once per import call** (never per row) forwarding
the caller's bearer token. It is sync so tests can monkeypatch this exact
module attribute with a plain callable (see tests/test_forecast_io.py,
mirroring the task brief's `monkeypatch.setattr(forecast_io,
"fetch_valid_material_codes", lambda *a, **k: {...})`) without needing an
async mock; the import endpoint bridges it onto the event loop via
`anyio.to_thread.run_sync`.

Row numbering in `error_rows`/ok-cell reporting matches the **physical Excel
row number** the user would see if they opened the file — the header is row
1, so the first data row is row 2. Users read this report to locate the bad
row in their spreadsheet, so it must line up with what Excel shows, not a
0-based or header-excluded count. A header-level problem (mismatched month
column) is reported as row 0 (there is no single data row it belongs to).
"""
from __future__ import annotations

import io
from decimal import Decimal, InvalidOperation

import httpx
from openpyxl import Workbook, load_workbook

from app.core.config import settings

TEMPLATE_HEADERS = ["Material Code", "Name"]


def fetch_valid_material_codes(token: str) -> set[str]:
    """Pull the full set of material codes known to mdm-api, once.

    Pages through GET /mdm/v1/materials (page_size=500) until exhausted.
    Forwards the caller's bearer token so this respects mdm-api's own authz
    (materials reads there are open to any authenticated role).
    """
    codes: set[str] = set()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(
        base_url=f"{settings.MDM_API_URL}/mdm/v1",
        timeout=settings.MDM_API_TIMEOUT_SECONDS,
        headers=headers,
    ) as http:
        page = 1
        page_size = 500
        while True:
            resp = http.get("/materials", params={"page": page, "page_size": page_size})
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            codes.update(item["code"] for item in items if item.get("code"))
            total = data.get("total", len(items))
            if not items or page * page_size >= total:
                break
            page += 1
    return codes


def parse_qty(raw) -> Decimal:
    """Tolerant, non-negative quantity parser.

    Strips thousands separators (`,`) and `$` before parsing, so `"20,000"`
    and `"$1,234.50"` both parse cleanly. Raises ValueError (with a
    human-readable message) for blank/non-numeric/negative input.
    """
    if raw is None:
        raise ValueError("quantity is required")
    text = str(raw).strip()
    if text == "":
        raise ValueError("quantity is required")
    cleaned = text.replace(",", "").replace("$", "").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"'{raw}' is not a valid number") from exc
    if value < 0:
        raise ValueError(f"quantity must be non-negative, got '{raw}'")
    return value


def build_template_workbook(months: list[str]) -> bytes:
    """Empty template: header row only — Material Code | Name | <18 months>."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Forecast"
    ws.append(TEMPLATE_HEADERS + list(months))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_export_workbook(months: list[str], rows: list[dict]) -> bytes:
    """Same shape as the template, filled with the current grid.

    `rows`: [{"material_code": str, "name": str | None, "cells": {month: Decimal}}]
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Forecast"
    ws.append(TEMPLATE_HEADERS + list(months))
    for row in rows:
        cells = row.get("cells") or {}
        values = [row["material_code"], row.get("name") or ""]
        for month in months:
            qty = cells.get(month)
            values.append(float(qty) if qty is not None else "")
        ws.append(values)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def parse_import_workbook(
    file_bytes: bytes, months: list[str], valid_codes: set[str],
) -> tuple[list[dict], list[dict]]:
    """Validate an uploaded xlsx against `months`/`valid_codes`.

    Returns (ok_cells, error_rows):
      - ok_cells: [{"material_code", "month", "qty": Decimal}] ready for the
        same upsert semantics as PUT .../cells.
      - error_rows: [{"row", "column", "reason"}]. `row` is the physical
        Excel row number (header = row 1, first data row = row 2). One row's
        problem never stops the rest — every other row is still evaluated.
    """
    wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = list(next(rows_iter) or ())
    except StopIteration:
        return [], [{"row": 0, "column": "", "reason": "workbook has no header row"}]

    header_months = header[2:2 + len(months)]
    header_months = ["" if h is None else str(h).strip() for h in header_months]

    error_rows: list[dict] = []
    valid_month_positions: list[int] = []
    for i, expected in enumerate(months):
        actual = header_months[i] if i < len(header_months) else ""
        if actual != expected:
            error_rows.append({
                "row": 0,
                "column": actual or f"(column {i + 3})",
                "reason": f"expected month header '{expected}', got '{actual or '(missing)'}'",
            })
        else:
            valid_month_positions.append(i)

    ok_cells: list[dict] = []
    # start=2: the header consumed by next() above is Excel row 1, so the
    # first row this loop sees is Excel row 2 — report the real row number.
    for row_num, raw_row in enumerate(rows_iter, start=2):
        if raw_row is None:
            continue
        row = list(raw_row)
        material_code = str(row[0]).strip() if row and row[0] is not None else ""
        if material_code == "":
            continue  # blank row — nothing to import

        if material_code not in valid_codes:
            error_rows.append({
                "row": row_num,
                "column": "Material Code",
                "reason": f"unknown material code '{material_code}'",
            })
            continue

        for i in valid_month_positions:
            col_index = 2 + i
            cell_value = row[col_index] if col_index < len(row) else None
            if cell_value is None or str(cell_value).strip() == "":
                continue  # blank cell — no data for this material/month
            try:
                qty = parse_qty(cell_value)
            except ValueError as exc:
                error_rows.append({"row": row_num, "column": months[i], "reason": str(exc)})
                continue
            ok_cells.append({"material_code": material_code, "month": months[i], "qty": qty})

    return ok_cells, error_rows
