"""MPS production-plan matrix -> xlsx (Production Plan Matrix design, Task 2;
week columns under a month grouping header, Task 8 of the 2026-08-12 weekly
rework).

Mirrors `app/services/forecast_io.py`'s export shape (openpyxl `Workbook`,
saved to an in-memory `io.BytesIO`, returned as raw bytes for the caller —
`app/api/v1/mps.py`'s `GET /runs/{id}/export` — to wrap in a `Response`).

Unlike the forecast grid (one row per material, one column per month, one
number per cell), the production-plan matrix needs FOUR numbers per
material/week — Demand, Available (opening stock), Planned, Gap — so each
product is rendered as four rows instead of one. Columns are WEEKS, not
`demand_month` (when the forecast wants it) and not `plan_week_month` either
(the shape this module had before Task 8) — a planner reading a *production*
plan needs to see the actual week the factory floor builds in, not just the
month it falls in. Row 1 groups those week columns under their owning month
(merged cells); row 2 carries each week's `week_label`; data starts at row 3.

**Columns come from the run's horizon, not from which weeks the lines
happened to land on.** `weeks_of_month` is walked for every month from
`run.horizon_start_month` for `run.horizon_months`, UNION every
`plan_week_month` actually present on `lines` (a lead-shifted pre-build can
legitimately land a line in a month before the nominal horizon start — see
`app/api/v1/mps.py::_planning_weeks`'s docstring — and dropping that column
would silently lose the line's data), spanned contiguously from the earliest
to the latest month so no month in between is skipped either. A week with no
line landed on it still gets a column of zeros rather than not existing —
the time axis must not silently skip.

Week labels are rendered under `run.week_calendar_mode` — the run's OWN
stored mode, never the live planning parameter (see `app/api/v1/mps.py`'s
module docstring, "A run snapshots the calendar it was generated under") —
so a released plan's export looks the same today as it will next year even
if somebody changes the factory's week-boundary convention in between.

`lines` are expected to already carry each line's demand context
(`demand_forecast`/`opening_stock`) — the caller builds these via
`app/api/v1/mps.py`'s `_build_demand_context`/`_line_response` (Task 1 of
this design), exactly the same computation `GET /runs/{id}` uses. This
module only aggregates and renders; it never re-derives net-requirement math
itself.

Decimal stays Decimal all the way from `MrpMpsLine.qty` / the demand context
through the per-(material, week) aggregation — the ONLY place a value is
coerced to `float` is the openpyxl cell boundary in `_scaled`, mirroring
`forecast_io.build_export_workbook`'s own "float only at the very edge"
discipline (see that module's docstring, M11 finding).

## Capacity-gap lines get their own row, excluded from Planned

A `capacity_gap` line is an un-placed shortfall pinned to the week it was
supposed to be made in — `app/services/mps_engine.py`'s docstring is explicit
that it "never touches the ledger" and is not a booked production slot.
Task 7's review flagged that the export nonetheless summed a gap line's
`qty` straight into "Planned", i.e. reported unmet demand as if it had been
built. That's wrong on its face — a planner exporting the plan to hand to
the factory floor must see production, not a number that quietly includes
what could not be produced.

Fixed here by giving Gap its own metric row instead of folding it into
Planned or dropping it. A silently-excluded gap is *safer* than counting it
as output but still hides the shortfall from a planner who only skims
Planned vs. Demand; an explicit Gap row keeps Planned honest (real production
only) while still surfacing exactly how much demand went unplaced and in
which week, so nothing needs cross-referencing the API response to see it.
The row is emitted for every product even when it is all zero, so the sheet
shape is stable across exports/products instead of coming and going with
whether a run happens to have any shortfalls.

## The (material, demand_month) dedup key, carried over per column

`demand_forecast`/`opening_stock` are snapshotted PER DEMAND MONTH and
copied onto every line of that month (mrp07). Under the month-column layout
this was safe to sum per `(material, plan_week_month)` cell because two
lines sharing a cell necessarily came from two different demand months.
Weekly broke that premise — one demand month spans several week ROWS, each
carrying the whole month's figure — and Task 7 fixed the resulting N x
inflation (measured 360 against a real 120) by counting each contribution
once per distinct `(material, plan_week_month, demand_month)` rather than
per line.

Week columns are a strictly finer grouping than `plan_week_month`, so the
same fix carries over unchanged in shape: replace the column key with the
WEEK a line landed on (`plan_week_start`) instead of the month. A demand
month whose lines land on several different weeks (whether inside one
plan-month or straddling two, via prebuild) contributes its snapshot to
EVERY week column it touches, once each per column — never zero times (a
straddled demand month must not vanish from either month's group) and never
more than once for the same (material, week, demand_month) combination. The
column still means "the demand behind what is built here", not a partition;
summing a Demand row across several weeks of one month is expected to
reproduce that month's figure that many times over — that is not this
module re-inflating anything, it is the same number shown once per place it
is relevant, exactly as the pre-Task-8 per-month columns already did.
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Protocol

from openpyxl import Workbook

from app.services.week_calendar import week_label, weeks_of_month

_KG_PER_TONNE = Decimal("1000")
_THREE_DP = Decimal("0.001")

# Row order within each product's four-row block.
_METRIC_ROW_LABELS = ("Demand", "Available", "Planned", "Gap")

# Data columns start at column 3 (1-indexed): col 1 = Product, col 2 = Metric.
_FIRST_DATA_COLUMN = 3


class _LineLike(Protocol):
    material_code: str
    demand_month: str
    plan_week_start: date
    plan_week_month: str
    qty: Decimal
    demand_forecast: Decimal
    opening_stock: Decimal
    capacity_gap: bool


def _scaled(value: Decimal, unit: str) -> float:
    """Decimal KG -> the cell's numeric value. `unit == "t"` divides by 1000
    and rounds to 3dp (design brief); `unit == "kg"` passes the raw KG value
    through unchanged. This is the one and only float() coercion in this
    module — everything upstream (aggregation) stays exact Decimal."""
    if unit == "t":
        value = (value / _KG_PER_TONNE).quantize(_THREE_DP, rounding=ROUND_HALF_UP)
    return float(value)


def _generate_months(start_month: str, count: int) -> list[str]:
    """Mirrors `app/api/v1/net_requirement.py::_generate_months` /
    `app/services/mps_engine.py::_generate_months` exactly. Duplicated here
    rather than imported for the same reason `mps_engine.py` duplicates it
    instead of reaching into the api layer: a services module should not
    depend on `app/api/v1/*`."""
    year, month = (int(p) for p in start_month.split("-"))
    months = []
    for i in range(count):
        m0 = month - 1 + i
        y = year + m0 // 12
        mm = m0 % 12 + 1
        months.append(f"{y:04d}-{mm:02d}")
    return months


def _month_span(first: str, last: str) -> list[str]:
    """Every 'YYYY-MM' from `first` to `last` inclusive. Mirrors
    `app/api/v1/mps.py::_month_span`, duplicated for the same reason as
    `_generate_months` above."""
    start = int(first[:4]) * 12 + int(first[5:7]) - 1
    end = int(last[:4]) * 12 + int(last[5:7]) - 1
    return [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(start, end + 1)]


def build_mps_matrix_workbook(
    run, lines: Iterable[_LineLike], unit: str, name_by_code: dict[str, str | None],
) -> bytes:
    """Group `lines` by material_code -> plan_week_start (the column).

    `planned` sums every non-gap line's qty landing on that exact week.
    `gap` sums every gap line's qty on that week (excluded from `planned` —
    see module docstring). `demand`/`available` are the frozen per-demand-
    month snapshot, added once per distinct `(material_code, plan_week_start,
    demand_month)` — see module docstring's dedup section.

    Sheet layout: row 1 = month grouping (merged across that month's week
    columns), row 2 = week labels (`week_label` under `run.week_calendar_mode`
    — the run's OWN stored mode), data from row 3: four rows per product
    (sorted by material_code) in Demand / Available / Planned / Gap order.
    `name_by_code` resolves the Product cell to the material's display name,
    falling back to the bare code when the map has no entry (mdm-api
    degrade-to-{} contract — see `app.services.mdm_client.resolve_material_names`'s
    docstring).

    `run` supplies `run_no` (sheet title), `horizon_start_month`/
    `horizon_months` (the nominal column span) and `week_calendar_mode` (how
    those columns are labelled and how a line's own week is resolved to a
    column — see module docstring).
    """
    lines = list(lines)
    mode = run.week_calendar_mode

    horizon_months = _generate_months(run.horizon_start_month, run.horizon_months)
    touched_months = {line.plan_week_month for line in lines}
    all_months = set(horizon_months) | touched_months
    span_months = _month_span(min(all_months), max(all_months)) if all_months else []

    # Ordered (month, week_start) pairs -- one per data column. A week
    # belongs to exactly one month under `weeks_of_month`'s own partition
    # (see week_calendar.py's docstring), so `week_start` is a safe unique
    # key across the whole grid.
    week_grid: list[tuple[str, date]] = [
        (month, w) for month in span_months for w in weeks_of_month(month, mode)
    ]

    # (material_code, plan_week_start) -> aggregate. `demand`/`available`
    # dedup on `counted` (see module docstring); `planned`/`gap` are plain
    # sums split by `capacity_gap`.
    planned: dict[tuple[str, date], Decimal] = {}
    gap: dict[tuple[str, date], Decimal] = {}
    demand: dict[tuple[str, date], Decimal] = {}
    available: dict[tuple[str, date], Decimal] = {}
    counted: dict[tuple[str, date], set[str]] = {}
    materials: set[str] = set()

    for line in lines:
        materials.add(line.material_code)
        key = (line.material_code, line.plan_week_start)

        if line.capacity_gap:
            gap[key] = gap.get(key, Decimal("0")) + line.qty
        else:
            planned[key] = planned.get(key, Decimal("0")) + line.qty

        seen = counted.setdefault(key, set())
        if line.demand_month not in seen:
            seen.add(line.demand_month)
            demand[key] = demand.get(key, Decimal("0")) + line.demand_forecast
            available[key] = available.get(key, Decimal("0")) + line.opening_stock

    wb = Workbook()
    ws = wb.active
    ws.title = run.run_no

    ws.cell(row=1, column=1, value="Product")
    ws.cell(row=1, column=2, value="Metric")
    ws.merge_cells(start_row=1, start_column=1, end_row=2, end_column=1)
    ws.merge_cells(start_row=1, start_column=2, end_row=2, end_column=2)

    # Row 1 (month grouping, merged across the month's own week columns) and
    # row 2 (week labels), walked month by month so each month's span is
    # contiguous and known up front for the merge.
    col = _FIRST_DATA_COLUMN
    for month in span_months:
        weeks = weeks_of_month(month, mode)
        if not weeks:
            continue
        start_col = col
        for w in weeks:
            ws.cell(row=2, column=col, value=week_label(w, mode))
            col += 1
        end_col = col - 1
        ws.cell(row=1, column=start_col, value=month)
        if end_col > start_col:
            ws.merge_cells(start_row=1, start_column=start_col, end_row=1, end_column=end_col)

    row = 3
    tables = {"Demand": demand, "Available": available, "Planned": planned, "Gap": gap}
    for material_code in sorted(materials):
        product_name = name_by_code.get(material_code) or material_code
        for label in _METRIC_ROW_LABELS:
            table = tables[label]
            ws.cell(row=row, column=1, value=product_name)
            ws.cell(row=row, column=2, value=label)
            col = _FIRST_DATA_COLUMN
            for _month, w in week_grid:
                value = table.get((material_code, w), Decimal("0"))
                ws.cell(row=row, column=col, value=_scaled(value, unit))
                col += 1
            row += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
