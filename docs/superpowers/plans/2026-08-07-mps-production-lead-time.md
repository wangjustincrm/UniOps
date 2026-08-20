# MPS Production Lead Time + Matrix Styling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the MPS a configurable production lead time (default 1 month) — a demand for month D is scheduled by default in `D − lead`, capacity shortfall pushes earlier, and a target that would fall before the current month is clamped to now and flagged `lead_shortfall`; plus Production Plan matrix styling (distinct row backgrounds, bold Planned).

**Architecture:** The change is centred in the pure `generate_mps` engine (add `lead_months` + `current_month` params, a `lead_shortfall` output, clamp the target and floor pre-build at `current_month`). Wiring (`create_run`/`recalculate`) passes the lead + current month and persists `production_lead_months` (run) / `lead_shortfall` (line). The matrix already keys by `plan_month`, so the lead shows up for free; only styling + a shortfall marker are added.

**Tech Stack:** Python 3.12 (pure engine + FastAPI + Alembic); React 19 + TS (mrp app); Pydantic v2; pytest.

## Global Constraints
- **Engine stays PURE:** `generate_mps` takes `current_month` as a parameter (create_run passes `datetime.now(timezone.utc)` as 'YYYY-MM'); NO clock/DB/IO inside the engine. Decimal for quantities; months are 'YYYY-MM' strings via the existing `_month_index`/`_shift_month` helpers (no date lib).
- **`lead=0` reproduces the exact old behaviour** (regression guard).
- **No unrelated MPS change:** capacity rules, shelf-life math, locked-line handling, release semantics unchanged except where the lead touches them.
- **Permissions:** run endpoints keep their existing gates (`mrp.run.execute` to generate/recalculate). English UI. Decimal-as-string on the wire.
- **mrp-api tests:** `cd mrp-api && JWT_SECRET_KEY=test-secret TEST_PG_PASSWORD=<pw> ALLOWED_ORIGINS='["http://localhost:5179"]' python -m pytest tests -q` (FOREGROUND, sequential; pw via `docker inspect uniops_postgres ... POSTGRES_PASSWORD`). Baseline **153 passed, 0 skipped**.
- **Frontend tsc:** `docker exec uniops_mrp_frontend sh -c 'cd /app && npx tsc -p tsconfig.app.json --noEmit'` clean except pre-existing baseUrl TS5101.
- **Alembic:** migration `mrp08_mps_lead_time` chains onto `mrp07_mps_line_demand_context`, single head, never stamp, apply in-container.
- **Branch:** `feature/mrp-phase0-foundations`; commit per task; no push/merge.

---

## File Structure
- `mrp-api/app/services/mps_engine.py` — MODIFY: `generate_mps` lead + current_month + `lead_shortfall` (T1).
- `mrp-api/tests/test_mps_engine.py` — MODIFY: lead tests (T1).
- `mrp-api/app/models/mps.py` — MODIFY: `MrpMpsRun.production_lead_months`, `MrpMpsLine.lead_shortfall` (T2).
- `mrp-api/alembic/versions/mrp08_mps_lead_time.py` — CREATE (T2).
- `mrp-api/app/api/v1/mps.py` — MODIFY: POST param + store + pass lead/current_month + persist lead_shortfall + response fields (T2).
- `mrp-api/tests/test_mps_api.py` — MODIFY: lead API tests (T2).
- `mrp/src/pages/mps/mpsApi.ts` — MODIFY: `MpsLine.lead_shortfall`, `MpsRun.production_lead_months` (T3).
- `mrp/src/pages/mps/ProductionMatrix.tsx` — MODIFY: row backgrounds, bold Planned, lead-shortfall yellow (T3).
- `mrp/src/pages/mps/ProductionPlanPage.tsx` — MODIFY: a "Lead (months)" input for Generate, default 1 (T3).

---

## Task 1: Engine — production lead time (pure)

**Files:** Modify `mrp-api/app/services/mps_engine.py`; Test `mrp-api/tests/test_mps_engine.py`.

**Interfaces:**
- Produces: `PlannedLine` gains `lead_shortfall: bool`. `generate_mps(demands, limits, shelf_life_months, safety_margin_fraction, lead_months: int, current_month: str, locked: list[PlannedLine] | None = None) -> list[PlannedLine]`.

**Behaviour** (per demand `(material, D, qty)`):
- `standard_target = _shift_month(D, -lead_months)`.
- `target = standard_target if _month_index(standard_target) >= _month_index(current_month) else current_month` (clamp to not-before-now).
- `lead_shortfall = _month_index(target) > _month_index(standard_target)`.
- Place at `target`; on capacity overflow, shift earlier month-by-month with the floor at `current_month` (NOT the demand month) — the existing pre-build search, re-based: its lower bound becomes `current_month`, and the shelf-life hard check uses the TOTAL offset `_month_index(D) − _month_index(plan_month) ≤ _max_prebuild_months(shelf_life, safety)`.
- `is_prebuild = _month_index(plan_month) < _month_index(standard_target)`.
- Unplaceable even at `current_month` → `capacity_gap=True`, `plan_month=target`, qty preserved, `lead_shortfall` as computed.
- Locked lines: unchanged (seeded, never moved; carry `lead_shortfall=False` unless already set).
- `lead_months=0` ⇒ `standard_target=D`, `target=max(D, current_month)`; if all demand months are ≥ current_month (the normal case), this is byte-identical to the old same-month-then-prebuild behaviour.

- [ ] **Step 1: Write failing tests** (`tests/test_mps_engine.py`), covering:

```python
from decimal import Decimal
from app.services.mps_engine import DemandItem, CapacityLimits, generate_mps

UNL = CapacityLimits(max_sku_count=None, max_output_qty=None)

def test_lead_schedules_one_month_before_demand():
    out = generate_mps([DemandItem("A", "2026-11", Decimal("10"))], UNL,
                       {"A": 24}, Decimal("0"), lead_months=1, current_month="2026-08")
    assert len(out) == 1
    assert out[0].plan_month == "2026-10"           # D - lead, capacity was free
    assert out[0].is_prebuild is False and out[0].lead_shortfall is False

def test_lead_clamped_to_current_month_flags_shortfall():
    # demand next month, lead 1 -> target = this month's predecessor = past -> clamp to now
    out = generate_mps([DemandItem("A", "2026-08", Decimal("10"))], UNL,
                       {"A": 24}, Decimal("0"), lead_months=1, current_month="2026-08")
    assert out[0].plan_month == "2026-08"
    assert out[0].lead_shortfall is True

def test_capacity_full_at_target_prebuilds_earlier_down_to_current_floor():
    demands = [DemandItem(c, "2026-11", Decimal("100")) for c in ("A", "B")]
    out = generate_mps(demands, CapacityLimits(max_sku_count=12, max_output_qty=Decimal("100")),
                       {"A": 24, "B": 24}, Decimal("0"), lead_months=1, current_month="2026-08")
    # target 2026-10 holds one; the other pre-builds to 2026-09 (earlier than standard target)
    assert any(l.is_prebuild and l.plan_month == "2026-09" for l in out)
    assert not any(l.plan_month < "2026-08" for l in out)   # never before current

def test_lead_zero_reproduces_same_month():
    out = generate_mps([DemandItem("A", "2026-11", Decimal("10"))], UNL,
                       {"A": 24}, Decimal("0"), lead_months=0, current_month="2026-08")
    assert out[0].plan_month == "2026-11" and out[0].is_prebuild is False and out[0].lead_shortfall is False

def test_shelf_life_shorter_than_lead_is_a_gap():
    # lead 2, shelf life 1 (minus safety) can't cover producing 2 months early -> gap
    out = generate_mps([DemandItem("A", "2026-11", Decimal("10"))], UNL,
                       {"A": 1}, Decimal("0"), lead_months=2, current_month="2026-08")
    assert out[0].capacity_gap is True and out[0].shelf_life_ok is False
```

- [ ] **Step 2: Run → FAIL** (signature/field).
- [ ] **Step 3: Implement** the lead into `generate_mps`: compute `standard_target`/`target`/`lead_shortfall`, re-base the placement + pre-build floor at `current_month`, keep the shelf-life hard check on the total `D − plan_month`, set `lead_shortfall` on every emitted line. Add `lead_shortfall: bool = False` to `PlannedLine`. Update the module docstring's flag section to define `lead_shortfall` and the lead.
- [ ] **Step 4: Run `pytest tests/test_mps_engine.py -q` → PASS** (all old + new).
- [ ] **Step 5: Commit** `feat(mrp): production lead time in the MPS engine`.

---

## Task 2: Wiring + persistence (run param, line flag, migration)

**Files:** Modify `mrp-api/app/models/mps.py`, `mrp-api/app/api/v1/mps.py`; Create `mrp-api/alembic/versions/mrp08_mps_lead_time.py`; Test `mrp-api/tests/test_mps_api.py`.

**Interfaces:**
- Produces: `MrpMpsRun.production_lead_months: int` (default 1), `MrpMpsLine.lead_shortfall: bool` (default False). `MpsRunCreate` gains `production_lead_months: int | None = None`. `MpsRunResponse` gains `production_lead_months`. `MpsLineResponse` gains `lead_shortfall: bool`.

- [ ] **Step 1: Write failing API tests** (`tests/test_mps_api.py`): `POST /mps/runs` with `production_lead_months: 1` stores it on the run and a produced line for demand month D lands in `plan_month == D-1` with `lead_shortfall` set correctly; omitting it defaults to 1; the run response echoes `production_lead_months`. Reuse the existing seed+generate helpers.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Migration `mrp08_mps_lead_time` (down_revision `mrp07_mps_line_demand_context`): `ALTER mrp_mps_runs ADD production_lead_months INTEGER NOT NULL DEFAULT 1`; `ALTER mrp_mps_lines ADD lead_shortfall BOOLEAN NOT NULL DEFAULT false`. Models: add the two columns. `MpsRunCreate.production_lead_months: int | None = None`; in `create_run`, `lead = body.production_lead_months if body.production_lead_months is not None else 1`, store on the run, and call `generate_mps(demands, limits, shelf_life, safety_margin, lead_months=lead, current_month=datetime.now(timezone.utc).strftime("%Y-%m"))`; persist `lead_shortfall=line.lead_shortfall` on each `MrpMpsLine`. Same in `recalculate_run` (read `run.production_lead_months`). Add `production_lead_months` to `MpsRunResponse` (and the explicit `_run_detail_response`/`get_run`/`export` run serialization) and `lead_shortfall` to `MpsLineResponse` + `_line_response`.
- [ ] **Step 4: Apply migration in-container** (`docker exec uniops_mrp_api alembic upgrade head`; single head `mrp08_mps_lead_time`). Run `pytest tests/test_mps_api.py -q` + full suite → PASS (0 skipped).
- [ ] **Step 5: Commit** `feat(mrp): persist production lead + lead_shortfall on runs/lines`.

---

## Task 3: Frontend — lead input + matrix styling + shortfall marker

**Files:** Modify `mrp/src/pages/mps/mpsApi.ts`, `ProductionMatrix.tsx`, `ProductionPlanPage.tsx`; Test: `tsc`.

- [ ] **Step 1: Types** — `mpsApi.ts`: add `lead_shortfall: boolean` to `MpsLine`, `production_lead_months: number` to the `MpsRun` type; `generate(forecastVersionId, opts?: { production_lead_months?: number })` — extend the existing generate call to send `production_lead_months` in the POST body (keep the current signature working; default omitted → backend defaults 1). `tsc` clean.
- [ ] **Step 2: Matrix styling** — `ProductionMatrix.tsx`: give the three metric sub-rows distinct backgrounds (Demand = `bg-neutral-50`, Available = `bg-primary-50/40`, Planned = `bg-success-50/50` — theme-appropriate, subtle); render the **Planned** row's values **bold** (`font-semibold`/`font-bold`). Add `lead_shortfall` to the cell aggregate (`shortfall = cell.cellLines.some(l => l.lead_shortfall)`); a shortfall (but not gap) Planned cell gets a **yellow/amber** marker (`bg-warning-50 text-warning-800` + a small icon) with `title="Produced later than the lead — no earlier capacity/time"`. Gap (red) takes precedence over shortfall (amber) when both. Keep the existing gap-red + lock icon. `tsc` clean.
- [ ] **Step 3: Lead input** — `ProductionPlanPage.tsx`: add a small numeric **"Lead (months)"** input in the toolbar next to Generate (default `1`, min `0`, ≤ e.g. `12`), stored in a `leadMonths` state; pass it to `mpsApi.generate(selectedVersionId, { production_lead_months: leadMonths })`. Gate with the existing `canExecute`. `tsc` clean.
- [ ] **Step 4: Commit** `feat(mrp): lead input + matrix row styling + lead-shortfall marker`.

---

## Self-Review (coverage vs spec)
- §2 lead configurable default 1 → T2 (run param) + T3 (input); clamp-to-now + shortfall → T1 (engine) + T2 (persist) + T3 (marker); is_prebuild vs lead_shortfall distinction → T1.
- §3 algorithm (target/clamp/pre-build floor/shelf-life-on-total/gap/locked/lead=0-regression) → T1 with explicit tests.
- §4 data (run.production_lead_months, line.lead_shortfall, migration mrp08) → T2.
- §5 display (lead shows via plan_month for free; shortfall yellow; row backgrounds; Planned bold) → T3.
- §6 out of scope (system-config UI, per-product lead, multi-level) → no task.
- §7 testing → T1 engine cases, T2 API cases, T3 tsc + manual.
- **Type consistency:** `PlannedLine.lead_shortfall`/`generate_mps(...lead_months,current_month...)` T1↔T2; `MpsLine.lead_shortfall`/`MpsRun.production_lead_months` T2↔T3; `generate(..., {production_lead_months})` T3.
