# Production Plan — Matrix View Redesign

**Status:** Approved direction (2026-08-07), pending spec review → plan.
Redesigns the Phase 1B Production Plan page (`mrp/src/pages/mps/ProductionPlanPage.tsx` + `MpsLineTable.tsx` + `CapacityBars.tsx`). The MPS engine, capacity rules, and the run/adjust/lock/recalculate/release endpoints are **unchanged** (except an additive read-shape extension + a new export endpoint).

## 1. Motivation

The current Production Plan display (capacity occupancy bars + a per-line table) isn't the shape the business wants. They want a **simple matrix table like Sales Forecast**: rows = products, columns = months, and for each product three numbers — **需求 (demand)**, **在库 (available stock)**, **计划生产 (planned production)** — plus **Excel export**.

## 2. Core semantics (locked with the business, 2026-08-07)

- **Columns are PRODUCTION months** (`plan_month` — where production physically lands), NOT demand months. A production record appears in the month it is scheduled to be produced. This is what the planner actually sees/controls ("我在生产计划里只能看到8月这个记录").
- **Each production record carries the demand context it was computed against.** For an MPS line produced in `plan_month` to satisfy demand due in `demand_month`:
  - **需求** = gross sales forecast for that line's `demand_month`.
  - **在库** = the projected available balance (PAB) entering that `demand_month` — the stock the engine netted against.
  - **计划生产** = the produced quantity (`line.qty`).
- **Worked example (business):** Product A, Oct demand 50 t; available stock entering Oct = 20 t; net need 30 t; initially planned Sept but capacity-shifted to **Aug**. The matrix shows, in the **Aug** column for A: **需求 50 / 在库 20 / 计划生产 30**, with a tooltip "for 2026-10 demand". The physical production month (Aug) is the column; the served demand month (Oct) is context.
- **PAB (在库) is computed on the demand timeline** and displayed against the production record. `opening_stock[first month] = current available stock (WMS + consignment)`, rolling forward month by month as forecast consumes it — exactly the existing `compute_net_requirements` rollforward (`NetRow.opening_stock`). The value shown against a production record = `opening_stock[line.demand_month]`.
- **Only products/months with actual production appear.** Demand fully covered by stock (no production needed) produces no MPS line and does not show here — that's the Sales Forecast's concern, not the Production Plan's.
- **Aggregation:** if one product has several lines landing in the **same** `plan_month` (serving different demand months), that cell sums the three numbers; the tooltip lists each served demand month.
- **Capacity-gap lines** (demand the engine could not place within capacity + shelf life) have `plan_month == demand_month` and are flagged. They appear in that month's column, rendered **red** with the unmet quantity, so unmet demand is visible rather than hidden.

## 3. Data & backend

The MPS run already stores everything needed: `mrp_mps_lines` (`material_code, demand_month, plan_month, qty, is_prebuild, capacity_gap, locked_by_planner, ...`), and the forecast + opening stock come from the run's `forecast_version_id` and `get_opening_stock_breakdown` / `compute_net_requirements` (the same helpers `create_run` already uses).

- **Extend `GET /mps/runs/{id}`** (additive) to include, per line, the demand context: `demand_forecast` (gross forecast for `demand_month`) and `opening_stock` (PAB entering `demand_month`). Compute these once per run in the GET handler by re-deriving the forecast-by-material + rolled-forward opening stock for the run's forecast version (same inputs `create_run` used), then attaching `demand_forecast`/`opening_stock` to each line by its `demand_month`. The existing `lines`, `capacity_occupancy`, `stats` fields stay (occupancy is now unused by the UI but harmless; keep it to avoid churn).
- **New `GET /mps/runs/{id}/export`** → an `.xlsx` (openpyxl, same pattern as the forecast export) of the matrix: product rows (three sub-rows each: Demand / Available / Planned), production-month columns, values in the caller's requested unit (query param `unit=kg|t`, default `t` to match the page). Gate `mrp.report.view`.

Numbers on the wire are Decimal-as-string (KG canonical); the frontend `Number()`s and applies the unit.

## 4. Frontend redesign (`ProductionPlanPage.tsx`)

- **Remove** `CapacityBars` and the current `MpsLineTable` per-line grid.
- **Add** a matrix table (a read-only, grouped table — reuse the sticky-scroll + KG/Tonne treatment from Sales Forecast; a bespoke read-only table is fine, it does not need the editable `MatrixGrid`):
  - Columns = the distinct `plan_month`s present in the run (sorted).
  - Each product = three stacked sub-rows labelled **需求 / 在库 / 计划生产** (English UI: **Demand / Available / Planned**).
  - Cell = the aggregated three numbers for (product, plan_month); empty where no production. Planned-production cells carry a tooltip naming the served demand month(s); capacity-gap cells render red.
  - Sticky header + sticky first column + in-page scroll (same fix as Sales Forecast's single-scroll-container).
  - **Unit toggle** KG / Tonne (default Tonne), same as Sales Forecast; values scale display-only (stored KG).
- **Keep** the workflow controls: forecast-version picker, **Generate**, **Recalculate**, **Confirm & Release** (with the non-latest-outlook warning), and the release summary dialog — all gated by their existing permissions (`mrp.run.execute` / `mrp.proposal.confirm`).
- **Keep manual adjust + row locking** (business-confirmed the workflow: adjust A from Sept → Aug, **lock**, Recalculate re-plans the rest around the locked Aug line):
  - Clicking a **Planned** cell opens the existing `AdjustDrawer` for the underlying line (edit its **physical `plan_month`** and/or qty) and a lock/unlock toggle. If a cell aggregates multiple lines, show a tiny line picker first (product + served demand month + qty) then adjust the chosen one. Locked lines show a lock icon and are held fixed by `Recalculate` (unchanged backend behaviour).
- **Export** button → `GET /mps/runs/{id}/export` (passing the current unit); download the blob (reuse the `saveBlob` helper).
- Empty-run state (no lines) keeps the existing "no production needed — stock covers the forecast" explanation.

## 5. Out of scope
- The MPS scheduling algorithm, capacity rules, pre-build/shelf-life logic — unchanged.
- No change to `mrp_demands` / release semantics.
- Phase 1C (material explosion, actual-output backfill) unchanged.

## 6. Testing
- Backend: `GET /runs/{id}` now returns `demand_forecast`/`opening_stock` per line matching the run's forecast + rolled-forward stock (a line for demand month D carries `forecast[D]` and `opening_stock[D]`); a capacity-gap line still returns with its flag; `GET /runs/{id}/export` returns a valid `.xlsx` with the matrix (unit-scaled). Existing MPS suite stays green.
- Frontend: `tsc` clean; manual click-through (E2E blocked) — the worked example renders in the plan_month column with the served-demand tooltip; adjust→lock→recalculate holds the locked line; unit toggle scales display; export downloads.
