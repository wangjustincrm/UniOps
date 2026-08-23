# Production Plan Matrix View — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Production Plan's capacity-bars + per-line table with a Sales-Forecast-style matrix (rows = products × three sub-rows Demand/Available/Planned, columns = production months), keeping adjust/lock/recalculate/release, plus Excel export.

**Architecture:** The MPS run already stores everything. Extend `GET /mps/runs/{id}` (additively) to attach each line's demand context (`demand_forecast`, `opening_stock`) by re-deriving the run's forecast + rolled-forward stock with the same helpers `create_run` uses. Add `GET /mps/runs/{id}/export` (openpyxl). The frontend builds the matrix from `run.lines` grouped by `(material_code, plan_month)`; a Planned cell opens the existing `AdjustDrawer` for the underlying line. The MPS algorithm, capacity rules, and adjust/lock/recalculate/release endpoints are unchanged.

**Tech Stack:** FastAPI + SQLAlchemy async + openpyxl (mrp-api); React 19 + TS + Tailwind + `@uniops/shell` (mrp app); Pydantic v2; pytest.

## Global Constraints

- **Quantity = Decimal, KG canonical.** Wire values are Decimal-as-string; frontend `Number()`s. The KG/Tonne toggle is DISPLAY ONLY (stored/returned values are KG; Tonne = ÷1000 for display, export `unit=t` scales in the workbook).
- **User-facing copy English** (Demand / Available / Planned). Comments may be Chinese.
- **Permissions (seeded):** `GET /runs/{id}` + `GET /runs/{id}/export` = `mrp.report.view`; Generate/Recalculate = `mrp.run.execute`; adjust/lock = `mrp.run.execute`; Confirm & Release = `mrp.proposal.confirm`. Gate every endpoint via `require_permission(...)`.
- **No MPS algorithm change.** `mps_engine.py`, capacity rules, `create_run`/`recalculate`/`adjust`/`confirm-release` logic unchanged. Only additive read-shape + export.
- **mrp-api tests:** `cd mrp-api && JWT_SECRET_KEY=test-secret TEST_PG_PASSWORD=<pw> ALLOWED_ORIGINS='["http://localhost:5179"]' python -m pytest tests -q` (run FOREGROUND, sequential; password `docker inspect uniops_postgres --format '{{range .Config.Env}}{{println .}}{{end}}' | grep POSTGRES_PASSWORD`). Baseline before this plan: **148 passed, 0 skipped**. "N skipped" = false pass.
- **Frontend tsc:** `docker exec uniops_mrp_frontend sh -c 'cd /app && npx tsc -p tsconfig.app.json --noEmit'` clean except the pre-existing `baseUrl` TS5101 line.
- **Branch:** `feature/mrp-phase0-foundations`; commit per task; do NOT push/merge.
- **Reuse existing pieces:** `AdjustDrawer.tsx` (adjust plan_month/qty), the `adjustLine`/lock endpoints, `saveBlob`, the Sales-Forecast single-scroll-container sticky pattern, the forecast export's openpyxl/StreamingResponse idiom (`forecast.py` `GET /versions/{id}/export` + `forecast_io.build_export_workbook`).

---

## File Structure
- `mrp-api/app/api/v1/mps.py` — MODIFY: add `demand_forecast`/`opening_stock` to `MpsLineResponse`, compute them in the run GET handler (T1); add `GET /runs/{id}/export` (T2).
- `mrp-api/app/services/mps_export.py` — CREATE: openpyxl matrix workbook builder (T2).
- `mrp-api/tests/test_mps_api.py` — MODIFY: line demand-context + export tests.
- `mrp/src/pages/mps/mpsApi.ts` — MODIFY: line fields + `exportRun` (T3).
- `mrp/src/pages/mps/ProductionMatrix.tsx` — CREATE: read-only matrix table (T4).
- `mrp/src/pages/mps/ProductionPlanPage.tsx` — MODIFY: mount matrix, remove bars/old table, wire unit toggle + export + adjust-from-cell (T5).
- `mrp/src/pages/mps/CapacityBars.tsx`, `MpsLineTable.tsx` — DELETE (T5, after confirming no other importer).

---

## Task 1: Attach demand context (Demand / Available) to each MPS line

**Files:** Modify `mrp-api/app/api/v1/mps.py`; Test `mrp-api/tests/test_mps_api.py`.

**Interfaces:**
- Produces: `MpsLineResponse` gains `demand_forecast: Decimal` and `opening_stock: Decimal`. `GET /mps/runs/{id}` returns them on every line: for a line with `demand_month = D`, `demand_forecast = forecast[material][D]` (gross forecast) and `opening_stock = NetRow.opening_stock[D]` (PAB entering D), from the run's `forecast_version_id`.

- [ ] **Step 1: Write the failing test** (`tests/test_mps_api.py`): seed a confirmed forecast (a product with forecast in some month D and little/no opening stock so a line is produced), generate a run, `GET /runs/{id}`; assert the produced line carries `demand_forecast == forecast[D]` and `opening_stock == the rolled-forward opening stock for D` (compute the expected value with `compute_net_requirements` in the test, or assert against the known seeded numbers). Reuse the file's existing seed helpers.

- [ ] **Step 2: Run → FAIL** (fields absent).

- [ ] **Step 3: Implement.** Add the two fields to `MpsLineResponse`. In the run GET handler (the one returning `MpsRunDetailResponse` with `lines` + `capacity_occupancy`), build a per-line context map ONCE:

```python
# Re-derive the run's demand basis exactly as create_run did, to attach the
# forecast + rolled-forward opening stock each line was computed against.
version = await db.get(ForecastVersion, run.forecast_version_id)
months = _generate_months(version.horizon_start_month, version.horizon_months)
by_material = await _load_forecast_by_material(db, version.id, months)
# material -> {month: (forecast_qty, opening_stock)}
ctx: dict[str, dict[str, tuple[Decimal, Decimal]]] = {}
for material_code, forecast_cells in by_material.items():
    forecast_by_month = {m: forecast_cells.get(m, Decimal("0")) for m in months}
    breakdown = await get_opening_stock_breakdown(db, material_code)
    ctx[material_code] = {
        row.month: (row.forecast_qty, row.opening_stock)
        for row in compute_net_requirements(forecast_by_month, breakdown.opening_stock)
    }
```

Then when serializing each line, set `demand_forecast`/`opening_stock` from `ctx.get(line.material_code, {}).get(line.demand_month, (Decimal("0"), Decimal("0")))`. (Construct the `MpsLineResponse` objects explicitly with these two extra values rather than pure `from_attributes`, since they aren't ORM columns.) `get_opening_stock_breakdown` raises `UomMismatchError` only for non-KG stock (finished goods are KG) — let it propagate (500) as it already can in `create_run`; not this task's concern.

- [ ] **Step 4: Run → PASS.** Also run `pytest tests/test_mps_api.py -q` fully (all existing MPS tests green).

- [ ] **Step 5: Commit** `feat(mrp): attach demand + opening-stock context to MPS lines`.

---

## Task 2: Excel export of the production matrix

**Files:** Create `mrp-api/app/services/mps_export.py`; Modify `mrp-api/app/api/v1/mps.py`; Test `mrp-api/tests/test_mps_api.py`.

**Interfaces:**
- Produces: `build_mps_matrix_workbook(run, lines, unit: str, name_by_code: dict[str,str|None]) -> bytes` in `mps_export.py`; `GET /mps/runs/{id}/export?unit=kg|t` (default `t`) → `StreamingResponse` xlsx, gated `mrp.report.view`.

- [ ] **Step 1: Write the failing test:** generate a run with ≥1 line; `GET /runs/{id}/export?unit=t` → 200, `content-type` = the openpyxl spreadsheet mime, `content-disposition` names an `.xlsx`; load the bytes with `openpyxl.load_workbook` and assert the sheet has product rows with Demand/Available/Planned sub-rows and a Planned value equal to the line qty ÷ 1000 (tonne). Also `unit=kg` returns the raw KG value.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `mps_export.py`.** Group `lines` by `material_code` then `plan_month`; columns = sorted distinct `plan_month`s; per (material, plan_month) aggregate `planned = sum(qty)`, `demand = sum(demand_forecast)`, `available = sum(opening_stock)`. Build an openpyxl `Workbook`: header row `Product | Metric | <plan_months...>`; for each product three rows (`Demand` / `Available` / `Planned`) with the material name in the Product cell (merged or repeated). Scale every numeric value by `1/1000` when `unit == "t"` (round to 3 dp). Return `wb`-saved bytes (`io.BytesIO`). In `mps.py`, add the endpoint mirroring `forecast.py`'s export (StreamingResponse, `content-disposition: attachment; filename=production-plan-<run_no>.xlsx`); resolve names via `resolve_material_names` (degrade to code) like the run list does. `unit` validated to `kg|t` (default `t`).

- [ ] **Step 4: Run → PASS** (+ full `test_mps_api.py`).

- [ ] **Step 5: Commit** `feat(mrp): export production plan matrix to xlsx`.

---

## Task 3: Frontend MPS API — line fields + export

**Files:** Modify `mrp/src/pages/mps/mpsApi.ts`; Test: `tsc`.

**Interfaces:** `MpsLine` gains `demand_forecast: string` and `opening_stock: string`. `mpsApi.exportRun(runId: string, unit: 'kg'|'t') => Promise<Blob>`.

- [ ] **Step 1:** Add the two `string` fields to the `MpsLine` type. Add `exportRun` using the mrp `api` client's blob GET (see how `forecastApi`/`saveBlob` do a blob download; if the `api` client lacks a blob getter, add a minimal `getBlob(path)` alongside `get`, mirroring the OA `api.getBlob` pattern the repo uses for authenticated downloads). `exportRun` calls `/mps/runs/${runId}/export?unit=${unit}`.
- [ ] **Step 2:** `tsc` clean.
- [ ] **Step 3: Commit** `feat(mrp): mps line context fields + export client`.

---

## Task 4: ProductionMatrix component (read-only matrix table)

**Files:** Create `mrp/src/pages/mps/ProductionMatrix.tsx`; Test: `tsc`.

**Interfaces:**
- Consumes: `lines: MpsLine[]`, `materialsByCode: Map<string, MaterialOption>` (for names), `unitScale: number` (1 or 1000), `formatValue: (kg:number)=>string`, `onAdjustCell: (lines: MpsLine[]) => void`, `readOnly: boolean`.
- Produces: the matrix table.

- [ ] **Step 1:** Build the component. Derive: `products = distinct material_code` (sorted, name from `materialsByCode`); `planMonths = sorted distinct line.plan_month`; a lookup `cell[material][plan_month] = { demand: Σdemand_forecast, available: Σopening_stock, planned: Σqty, gap: any(capacity_gap), demandMonths: sorted distinct demand_month, lines: [...] }` (all sums in KG; display via `formatValue`). Render a single `overflow-auto` fixed/responsive-height scroll container (same single-container sticky pattern as Sales Forecast's fix) with: sticky header row (Product | Metric | plan-month columns), sticky first column. Each product = three rows: **Demand**, **Available**, **Planned**. A Planned cell that has production is a `<button>` (unless `readOnly`) with a `title` = "For {demandMonths.join(', ')} demand" that calls `onAdjustCell(cell.lines)`; capacity-gap cells render red (`text-danger-*`/`bg-danger-50`) with the unmet qty; locked cells show a lock icon (`any(line.locked_by_planner)`). Empty cells render `—`. Values formatted through `formatValue` (KG→display), so the parent's unit toggle drives it. Wrap in `overflow-x-auto`; body never scrolls horizontally.
- [ ] **Step 2:** `tsc` clean.
- [ ] **Step 3: Commit** `feat(mrp): production plan matrix table component`.

---

## Task 5: Rewire ProductionPlanPage around the matrix + export + unit toggle

**Files:** Modify `mrp/src/pages/mps/ProductionPlanPage.tsx`; Delete `CapacityBars.tsx`, `MpsLineTable.tsx` (after grep-confirming no other importer); Test: `tsc`.

**Interfaces:** Consumes `ProductionMatrix` (T4), `mpsApi.exportRun` (T3), existing `AdjustDrawer`, `materialsByCode`, `usePermissions`.

- [ ] **Step 1:** Remove `<CapacityBars>` and `<MpsLineTable>` and their imports. Mount `<ProductionMatrix lines={run.lines} materialsByCode={materialsByCode} unitScale={displayUnit==='t'?1000:1} formatValue={fmt} readOnly={isReleased} onAdjustCell={handleAdjustCell} />` when `run && run.lines.length > 0` (keep the existing "no production needed" empty state for 0 lines).
- [ ] **Step 2:** Add `displayUnit` state ('kg'|'t', default 't', localStorage key `mrp.plan.displayUnit`) + a KG/Tonne toggle in the toolbar (same styling/44px as Sales Forecast's) + `formatValue` = KG→display (tonne = /1000, 3dp).
- [ ] **Step 3:** `handleAdjustCell(lines)`: if `lines.length === 1`, set that line as the `AdjustDrawer` target (existing `adjustTarget` state + `<AdjustDrawer>` already on the page — keep it); if multiple, open a tiny inline picker (product + served demand month + qty per line) and set the chosen one. Keep the existing lock/unlock via the drawer/`adjustLine`. The drawer edits the physical `plan_month` + qty (unchanged).
- [ ] **Step 4:** Add an **Export** button (gated `mrp.report.view`, i.e. always visible to viewers) → `await mpsApi.exportRun(runId, displayUnit)` then `saveBlob(blob, null, 'production-plan.xlsx')`; three-state (loading→toast), never silent.
- [ ] **Step 5:** Keep Generate / Recalculate / Confirm & Release + the non-latest-outlook warning + release summary + `key={run.id}` unchanged. `tsc` clean. `grep -r "CapacityBars\|MpsLineTable" mrp/src` → only the deleted files / comments; delete the two files. Commit `feat(mrp): Production Plan matrix view + export, retire capacity bars & line table`.

---

## Self-Review (coverage vs spec)
- §2 columns=plan_month, per-record Demand(forecast)/Available(PAB)/Planned(qty), served-demand tooltip, aggregation, gap-red → T1 (backend context) + T4 (matrix) + T5 (wiring).
- §3 backend: line context → T1; export endpoint → T2.
- §4 frontend: matrix + sticky + unit + gap + tooltip → T4; keep Generate/Recalc/Release/adjust/lock, remove bars/table, export button, unit toggle → T5.
- §5 out of scope: MPS algorithm / release semantics untouched (T1/T2 additive; T5 keeps the action endpoints) — no task changes them.
- §6 testing: T1/T2 backend tests; tsc + manual for T3–T5.
- **Type consistency:** `MpsLine.demand_forecast/opening_stock` (string wire) T1↔T3↔T4; `exportRun(runId, unit)` T3↔T5; `onAdjustCell(lines)` T4↔T5.
- **Open point folded in:** the KG/Tonne default is Tonne with localStorage (T5), matching Sales Forecast; the matrix is a bespoke read-only table (NOT the editable MatrixGrid) per spec §4.
