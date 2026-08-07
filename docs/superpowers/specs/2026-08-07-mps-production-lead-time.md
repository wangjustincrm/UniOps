# MPS Production Lead Time + Matrix Styling

**Status:** Approved direction (2026-08-07), pending spec review → plan. Amends the Phase 1B MPS engine (`app/services/mps_engine.py`) + `create_run`/`recalculate` + the Production Plan matrix. Capacity rules, shelf-life, release semantics otherwise unchanged.

## 1. Motivation

Production leads sales — a finished good demanded in month D must normally be produced **at least one month earlier**. The current MPS defaults `plan_month = demand_month` (same-month) and only shifts earlier when capacity forces it, so an unconstrained demand shows as produced in its own demand month (e.g. Nov demand produced in Nov), which is wrong. Add a configurable **production lead time** (default 1 month): a demand for month D is scheduled by default in `D − lead`, and capacity shortfall pushes it earlier still.

## 2. Decisions (locked 2026-08-07)

- **Lead time** is a **configurable parameter, default 1 month** (like the shelf-life safety margin: a generate-time parameter stored on the run; a future system-config UI can set the default). `production_lead_months`.
- **Too-late clamp:** if `D − lead` falls before the current month (can't produce in the past), the line is scheduled in the **earliest available month (current month)** and flagged **lead-shortfall** (a warning, produced anyway) — never silently dropped.
- **Distinct from pre-build:** producing at the standard `D − lead` is NORMAL (not pre-build). `is_prebuild` means produced EARLIER than `D − lead` because capacity was full at/after the target. `lead_shortfall` means produced LATER than `D − lead` because `D − lead` was already in the past.

## 3. Algorithm (`generate_mps`)

New signature: `generate_mps(demands, limits, shelf_life_months, safety_margin_fraction, lead_months: int, current_month: str, locked=None)`. Per demand `(material, D, qty)`:

1. `standard_target = D − lead_months` (month arithmetic on 'YYYY-MM' via the existing local helpers).
2. `target = max(standard_target, current_month)` — never schedule before now.
3. `lead_shortfall = target > standard_target` (the clamp bit).
4. Place `qty` at `target` if both capacity limits allow; else shift **earlier** month by month down to `current_month` (the existing pre-build search, but the floor is now `current_month`, not the demand month), each hop still passing the **shelf-life hard check** on the TOTAL offset `D − plan_month ≤ floor(shelf_life × (1 − safety_margin))` and unknown shelf life never pre-built past the standard target.
5. `is_prebuild = plan_month < standard_target`.
6. If it cannot be placed even at `current_month` → `capacity_gap = True`, `plan_month = target` (so it renders in a sensible column), qty preserved, never dropped.
7. Locked lines are seeded and never moved (unchanged).

Notes: `current_month` keeps the engine PURE — it's a parameter (create_run passes `datetime.now(timezone.utc)` as 'YYYY-MM'), never read from a clock inside the engine. The shelf-life budget now has to cover the lead too: if `lead_months` alone exceeds `floor(shelf_life × (1 − safety))`, even the standard target violates shelf life → that demand becomes a gap (shelf-life-caused), surfaced as today.

## 4. Data

- Add `production_lead_months INT` to `mrp_mps_runs` (default 1). `POST /mps/runs` gains an optional `production_lead_months` (default 1), stored on the run; `create_run`/`recalculate_run` pass it + the current month into `generate_mps`.
- Add `lead_shortfall BOOLEAN` (default false) to `mrp_mps_lines`, set by the engine, persisted, and returned on `MpsLineResponse`.
- Migration `mrp08_mps_lead_time` (chains onto `mrp07_mps_line_demand_context`, single head).

## 5. Display (Production Plan matrix)

- With lead applied, a demand for D now renders in the `D − lead` column (e.g. Nov demand → Oct column). No frontend logic change needed for this — the matrix already keys by `plan_month`, which now reflects the lead.
- **Lead-shortfall** cells: a distinct **yellow/warning** marker (separate from the capacity-gap red), tooltip "Produced later than the 1-month lead — no earlier capacity/time".
- **Row background colours (business ask):** the three metric sub-rows get distinct backgrounds — **Demand**, **Available**, **Planned** each a subtly different tint (e.g. neutral / light-blue / light-green, theme-appropriate).
- **Planned value bold** (business ask): the Planned row's numbers render bold (it's the actionable output).

## 6. Out of scope
- A system-wide MRP-parameters config UI (lead is a run parameter defaulting to 1 for now).
- Per-product lead time (single global lead this phase; materials-master per-product lead is a future option).
- Multi-level lead (component lead times) — Phase 1C.

## 7. Testing
- **Engine** (`test_mps_engine.py`): a demand with spare capacity is scheduled at `D − lead` (not D); `lead=1` on a Nov demand → Oct plan_month; a demand whose `D − lead` is before `current_month` → scheduled at `current_month` with `lead_shortfall=True`; capacity full at the target → pre-build earlier (`is_prebuild`, down to `current_month` floor); shelf-life shorter than the lead → shelf-life gap; `lead=0` reproduces the old same-month behaviour. Locked lines untouched.
- **API** (`test_mps_api.py`): `production_lead_months` stored on the run + default 1; a generated line carries the right `plan_month` (D − lead) and `lead_shortfall`; existing MPS + matrix + export tests stay green.
- Frontend `tsc` clean; manual: Nov demand shows in the Oct column; shortfall cells yellow; row backgrounds distinct; Planned bold.
