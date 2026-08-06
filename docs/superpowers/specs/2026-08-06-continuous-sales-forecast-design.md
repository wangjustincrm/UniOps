# Continuous Sales Forecast — Design

**Status:** Approved direction (2026-08-06), pending spec review → implementation plan.
**Supersedes** the discrete-version editing model of Phase 1A's Sales Forecast (`docs/superpowers/specs/2026-08-03-mrp-subsystem-design.md` §6.6 page 1). MPS (Phase 1B) is **unchanged**.

## 1. Motivation

Today each sales forecast is a discrete `mrp_forecast_versions` snapshot built (optionally copied) from scratch. Consequences the business flagged:

- **Continuity is broken.** A forecast built in June and one built in July are disconnected islands. There is no single living demand series that simply rolls forward.
- **Change impact is invisible.** If a month's number is later revised (or, once Phase 1C lands, actual output diverges from plan — planned SKU-A 50, produced 60), the downstream effect on later months cannot be traced across disconnected version snapshots.

**The idea (business, 2026-08-06):** Sales Forecast is **one continuous table** (product × absolute month, a single living dataset). The "18-month forecast" is a **sliding window** over that table — you *generate* a concrete 18-month outlook from the window, but that outlook is just an immutable **snapshot/view**, not the source of truth. MPS follows the same principle (it already consumes an immutable snapshot).

## 2. Decisions locked in brainstorming (2026-08-06)

1. **History granularity = current value + change log.** The continuous table stores only the *current* forecast value per (product, month). A separate append-only change log records every edit (who / when / month / old→new), so "how did this month's forecast evolve" is answerable without full-table snapshots.
2. **Edit the big table; the window is a read-only View + a snapshot handle.** Planners edit the continuous table directly. "Generate Outlook" **freezes** the current `[anchor, anchor+18)` slice into an immutable snapshot (with id + timestamp + anchor). MPS consumes that snapshot. Later edits to the big table do not change an already-generated outlook until it is regenerated.
3. **Scope = continuous-forecast foundation only.** This phase delivers the continuous table + change log + window view + Generate-Outlook (freeze → snapshot). **MPS is not modified.** Actual-output backfill, showing actual values in past months, and variance carry-forward into future demand are **Phase 1C** — they will write into this same continuous table later.
4. **Reuse `mrp_forecast_versions` as the snapshot** rather than inventing a new snapshot table: it is already immutable and already consumed by MPS, so this is the smallest change. It gains a `source_anchor_month` column to mark it as a window-freeze product rather than a hand-built version.
5. **Past months are read-only** in the continuous table this phase (they become the write target for Phase 1C actuals).

## 3. Architecture

```
            ┌─────────────────────────────────────────────┐
 EDIT ───▶  │  mrp_demand_series  (product × month, live)  │ ◀── Phase 1C writes actuals/carry-forward here (future)
            │  the single source of truth, unbounded time  │
            └───────────────┬─────────────────────────────┘
                            │ every cell edit appends
                            ▼
            ┌─────────────────────────────────────────────┐
            │  mrp_forecast_change_log  (append-only)      │  who/when/month/old→new  → "how did this evolve"
            └─────────────────────────────────────────────┘
                            │  Generate Outlook: freeze [anchor, anchor+18)
                            ▼
            ┌─────────────────────────────────────────────┐
            │  mrp_forecast_versions/_lines  (immutable    │  ← REUSED as the snapshot; MPS consumes unchanged
            │  snapshot, + source_anchor_month)            │
            └───────────────┬─────────────────────────────┘
                            ▼   (Phase 1B, unchanged)
                        MPS run  →  mrp_demands
```

## 4. Data model

### 4.1 `mrp_demand_series` (NEW — the living continuous table)

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| material_code | str(50), indexed | finished good |
| month | char(7) `YYYY-MM`, indexed | **absolute, unbounded** past+future |
| qty | Numeric(18,3) | current forecast quantity |
| uom | str(10) default `KG` | planning UOM (KG; enforced same as Phase 1B Task 0) |
| created_at / updated_at | | |

- **Unique (material_code, month).** Sparse: a row exists only for a non-zero cell (mirrors today's `mrp_forecast_lines` sparsity). Deleting a value = deleting the row.
- **No `version_id`.** This is the one living dataset, not a per-version copy.

### 4.2 `mrp_forecast_change_log` (NEW — append-only audit of edits)

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| material_code | str(50), indexed | |
| month | char(7), indexed | |
| old_qty | Numeric(18,3) nullable | null = cell had no value before |
| new_qty | Numeric(18,3) nullable | null = value cleared |
| source | str(20) default `manual` | `manual` \| `import` \| `carry_forward` (1C) |
| changed_by | uuid nullable | JWT sub |
| changed_at | timestamptz | server time |

- Written on **every** committed change to a series cell (single-cell edit, paste, import). One row per (material, month) actually changed. No row when a write is a no-op.

### 4.3 `mrp_forecast_versions` (REUSED — now the outlook snapshot)

- Add column `source_anchor_month char(7) NULL` — the window anchor a snapshot was frozen from (`NULL` for any legacy hand-built version).
- **Behavioural change:** a version is now created **already immutable** by Generate Outlook (status `confirmed` at birth; no draft/confirm two-step). The old "exactly one confirmed at a time, confirming supersedes the prior" invariant is **removed** — every outlook is a permanent snapshot and several coexist (that history is exactly what makes change-impact visible). `mrp_forecast_lines` is unchanged in shape; it now holds the frozen `[anchor, anchor+18)` copy.

## 5. Editing UI (Sales Forecast page, rebuilt)

- **One continuous grid** (product × month), reusing the existing `MatrixGrid` (direct edit + paste + add-row + focus-select, all already built). Rows = finished goods; columns = a month range.
- **Month range:** the grid can't render unbounded time, so it loads a practical window — default **`[current_month − 3, current_month + 24]`** — with the active **18-month outlook window `[anchor, anchor+18)` visually highlighted** (a subtle band + header markers). A "Load earlier / later months" control extends the rendered range; the underlying series is unbounded.
- **Past months** (`month < current_month`) render **read-only** (frozen cells) — they are history and the Phase 1C actuals target. **Current + future months** are editable.
- **Persistence:** edits commit to `mrp_demand_series` and append to `mrp_forecast_change_log`. No "draft vs confirmed version" concept remains — you maintain one living table. (A debounced autosave or an explicit "Save" is a UI detail settled in the plan; the change log is written server-side on the upsert either way.)
- **Change history:** clicking a cell can reveal its change log ("last changed 2026-07-14: 50 → 60 by …"). Minimal version: a per-cell history popover reading `GET /series/change-log`.

## 6. Generate Outlook → snapshot → MPS

- A **`Generate 18-mo Outlook`** action on the forecast page: pick **anchor month** (default = current month) + horizon (default 18). It freezes `[anchor, anchor+horizon)` from `mrp_demand_series` into a new immutable `mrp_forecast_versions` (+ `_lines`), stamped `source_anchor_month = anchor`, `status = confirmed`.
- **Production Plan (Phase 1B) is unchanged:** its version picker already lists confirmed versions; the planner selects an outlook snapshot and Generates the MPS exactly as today. `mps.py` needs no change.
- **Comparing outlooks** (two snapshots side by side — "last month's outlook vs this month's") is the primary "discover the impact" surface. This phase persists the snapshots and their `source_anchor_month`; a dedicated compare view is **deferred** (out of scope below) — the data to build it exists after this phase.

## 7. Backend API (mrp-api)

New endpoints (mirror existing `forecast.py` auth: read `mrp.report.view`, write `mrp.demand.write`):

- `GET /series?from=YYYY-MM&to=YYYY-MM` → the continuous grid slice (materials × months in range), same response shape the current grid endpoint returns so `MatrixGrid` reuse is trivial.
- `PUT /series/cells` (bulk upsert) → apply a set of `(material_code, month, qty)` changes; server computes old→new per cell, writes `mrp_demand_series` and appends `mrp_forecast_change_log` in one transaction; rejects any `month < current_month` (past = read-only) and any `uom != KG`.
- `GET /series/change-log?material_code=&month=` → a cell's (or a material's) edit history, newest first.
- `POST /outlook` `{anchor_month, horizon_months=18}` → freeze the window into a confirmed `mrp_forecast_versions` (+ `_lines`); returns the version.

Retained/adjusted:

- Version **read** endpoints stay (MPS picker, viewing a frozen snapshot read-only).
- The old per-version **grid write / draft / confirm** endpoints are **removed** (editing moved to the series). Net-requirement + MPS endpoints unchanged.

## 8. Migration & impact on existing phases

- **Migration `mrp05`:** create `mrp_demand_series`, `mrp_forecast_change_log`; `ALTER mrp_forecast_versions ADD source_anchor_month`.
- **Seed the series** from the existing confirmed version's lines (dev has one real confirmed version; production has no real MRP data yet). Legacy versions keep `source_anchor_month = NULL`.
- **Phase 1A:** the forecast page UI is substantially rebuilt (version switcher/New/Save/Confirm → continuous table + Generate Outlook). The grid backend shifts from per-version to series-based; the `MatrixGrid` component and paste/undo modules are reused as-is.
- **Phase 1B (MPS):** **no code change.** It consumes a confirmed version, which Generate Outlook still produces. The "one active released plan" semantics of `mrp_demands` (release replaces all `demand_type='mps'`) is preserved.

## 8b. No-BOM finished goods (business add, 2026-08-06)

A finished good can be forecast **before its BOM exists** (new product, BOM not yet built in NC). This must be a first-class, non-blocking case, not an error:

- **Forecast:** entry is allowed for a product with no approved BOM (already true — the series never checks BOM). The Sales Forecast grid **visually flags** such rows (a distinct tint + a "No BOM" badge) so the planner sees the gap without being blocked.
- **Outlook / MPS:** the product freezes into the outlook snapshot and is planned by MPS normally (MPS schedules finished-good quantities and never looks at BOMs). Its planned line carries the same "No BOM" marker.
- **Material requirements (Phase 1C):** a no-BOM finished good **does not participate in material-requirement explosion** — it contributes zero component demand and is surfaced as "materials not calculated (no BOM)", **not** as the hard `missing_bom` error the BOM Explorer raises for a product that *should* explode. When its BOM is later built, the next outlook/MRP run picks it up automatically.
- **Source of truth:** "has a BOM" is owned by mdm-api (an approved `boms` row for the product code). Consumers (forecast page, MPS page, 1C engine) query a single batch endpoint rather than each re-deriving it.

## 9. Out of scope (explicitly deferred to Phase 1C)

- Actual-output backfill; showing **actual** values in past months.
- Variance (plan vs actual) **carry-forward** into future demand (will append to the series with `source='carry_forward'`).
- A dedicated **outlook-comparison / impact-diff** view (data is captured this phase; the view is later).
- Multiple consignment warehouses; non-KG demand units.

## 10. Testing approach

- **Series upsert + change-log** (API): a cell change writes the series and appends exactly one log row with correct old→new; a no-op writes nothing; a `month < current_month` write is rejected; a non-KG uom is rejected.
- **Generate Outlook** (API): freezing `[anchor, +18)` produces a confirmed version whose lines equal the series slice; regenerating after an edit yields a new snapshot while the prior snapshot is unchanged (immutability).
- **Net requirement / MPS**: unchanged; existing Phase 1B suite must stay green (a generated outlook still drives an MPS run end-to-end).
- Frontend: `tsc` clean; manual click-through (E2E blocked) for the continuous grid, past-month read-only, highlight band, Generate Outlook.

## 11. Open questions (resolve during planning, not blocking)

- Autosave-on-blur vs an explicit Save button for series edits (both write the change log server-side; UX preference).
- Default rendered month range bounds and the "load more months" affordance.
- Whether the per-cell change-history popover ships this phase or is a fast follow.
