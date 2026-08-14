"""MPS production-plan matrix -> xlsx (Production Plan Matrix design, Task 2).

Mirrors `app/services/forecast_io.py`'s export shape (openpyxl `Workbook`,
saved to an in-memory `io.BytesIO`, returned as raw bytes for the caller —
`app/api/v1/mps.py`'s `GET /runs/{id}/export` — to wrap in a `Response`).

Unlike the forecast grid (one row per material, one column per month, one
number per cell), the production-plan matrix needs THREE numbers per
material/month — Demand, Available (opening stock), Planned — so each
product is rendered as three rows instead of one. Columns are the run's
lines' **`plan_week_month`** (the month production is scheduled in), not
`demand_month`
(when the forecast wants it) — those two can differ whenever the engine
pre-builds a line ahead of its demand month for shelf-life reasons (see
`app/services/mps_engine.py`'s docstring), and this export is specifically
the *production* plan, i.e. what the factory floor should build and when.

`lines` are expected to already carry each line's demand context
(`demand_forecast`/`opening_stock`) — the caller builds these via
`app/api/v1/mps.py`'s `_build_demand_context`/`_line_response` (Task 1 of
this design), exactly the same computation `GET /runs/{id}` uses. This
module only aggregates and renders; it never re-derives net-requirement math
itself.

**Still month-columned.** The weekly rework only renamed the field this
reads (`plan_month` -> `plan_week_month`) so the endpoint keeps working
against weekly lines; design §5.1's week columns with a month grouping
header are a separate change and are not here yet. Columns are therefore
still one per month, now derived from each line's plan WEEK's owning month.

Decimal stays Decimal all the way from `MrpMpsLine.qty` / the demand context
through the per-(material, plan_week_month) aggregation — the ONLY place a value
is coerced to `float` is the openpyxl cell boundary in `_scaled`, mirroring
`forecast_io.build_export_workbook`'s own "float only at the very edge"
discipline (see that module's docstring, M11 finding).
"""
from __future__ import annotations

import io
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Protocol

from openpyxl import Workbook

_KG_PER_TONNE = Decimal("1000")
_THREE_DP = Decimal("0.001")

# Row order within each product's three-row block.
_METRIC_ROWS = (("Demand", "demand"), ("Available", "available"), ("Planned", "planned"))


class _LineLike(Protocol):
    material_code: str
    plan_week_month: str
    qty: Decimal
    demand_forecast: Decimal
    opening_stock: Decimal


def _scaled(value: Decimal, unit: str) -> float:
    """Decimal KG -> the cell's numeric value. `unit == "t"` divides by 1000
    and rounds to 3dp (design brief); `unit == "kg"` passes the raw KG value
    through unchanged. This is the one and only float() coercion in this
    module — everything upstream (aggregation) stays exact Decimal."""
    if unit == "t":
        value = (value / _KG_PER_TONNE).quantize(_THREE_DP, rounding=ROUND_HALF_UP)
    return float(value)


def build_mps_matrix_workbook(
    run, lines: Iterable[_LineLike], unit: str, name_by_code: dict[str, str | None],
) -> bytes:
    """Group `lines` by material_code -> plan_week_month, aggregating
    planned=Σqty, demand=Σdemand_forecast, available=Σopening_stock per cell
    (a (material, plan_week_month) pair can have more than one line whenever the
    engine split demand across a gap-then-placed pair or similar — summing
    is the same "one number per cell" contract the forecast grid uses).

    Sheet layout: header row `Product | Metric | <sorted distinct plan
    months>`,
    then per product (sorted by material_code) three rows in Demand / Available
    / Planned order. `name_by_code` resolves the Product cell to the
    material's display name, falling back to the bare code when the map has
    no entry (mdm-api degrade-to-{} contract — see
    `app.services.mdm_client.resolve_material_names`'s docstring).

    `run` is unused for the numbers themselves but names the sheet
    (`run.run_no`, e.g. "MPS-20260907-0001" — well under openpyxl's 31-char
    sheet-title limit) so a planner with several exports open can tell them
    apart by tab.
    """
    grouped: dict[str, dict[str, dict[str, Decimal]]] = {}
    months: set[str] = set()
    for line in lines:
        months.add(line.plan_week_month)
        cell = grouped.setdefault(line.material_code, {}).setdefault(
            line.plan_week_month, {"demand": Decimal("0"), "available": Decimal("0"), "planned": Decimal("0")},
        )
        cell["demand"] += line.demand_forecast
        cell["available"] += line.opening_stock
        cell["planned"] += line.qty

    sorted_months = sorted(months)

    wb = Workbook()
    ws = wb.active
    ws.title = run.run_no
    ws.append(["Product", "Metric"] + sorted_months)

    for material_code in sorted(grouped):
        product_name = name_by_code.get(material_code) or material_code
        cells_by_month = grouped[material_code]
        for label, key in _METRIC_ROWS:
            row = [product_name, label]
            for month in sorted_months:
                cell = cells_by_month.get(month)
                value = cell[key] if cell is not None else Decimal("0")
                row.append(_scaled(value, unit))
            ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
