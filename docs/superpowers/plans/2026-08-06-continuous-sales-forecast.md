# Continuous Sales Forecast — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the discrete-version Sales Forecast editing model with one continuous demand series (product × absolute month) plus a change log; the 18-month forecast becomes a read-only window, and "Generate Outlook" freezes the window into an immutable snapshot (reusing `mrp_forecast_versions`) that MPS consumes unchanged.

**Architecture:** New `mrp_demand_series` (living, unbounded-time, no version_id) is the single editing surface; every cell write appends to `mrp_forecast_change_log`. A `POST /outlook` endpoint copies the current `[anchor, anchor+18)` slice into a `mrp_forecast_versions` snapshot (`status='confirmed'`, `source_anchor_month` set), which Phase 1B's MPS already consumes. The Sales Forecast page is rebuilt around the continuous grid (reusing the existing `MatrixGrid`), with past months read-only. MPS (Phase 1B) is not modified.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (mrp-api, Python 3.12); React 19 + TS + Tailwind + `@uniops/shell` (mrp app, Vite 5179); Pydantic v2; pytest.

## Global Constraints

- **Quantity = Decimal end to end.** Never float. Pydantic serializes Decimal to a JSON string; the frontend `Number()`s it before arithmetic (`feedback_uniops_decimal_as_string`).
- **Planning UOM is KG.** Every series cell is KG; reject non-KG writes (same invariant as Phase 1B Task 0).
- **Past months are read-only.** Reject any series write where `month < current_month`. `current_month` is injectable (a parameter defaulting to `datetime.now(timezone.utc)`) so tests are deterministic.
- **User-facing copy is English only.** Comments may be Chinese.
- **Permissions (already seeded):** read `mrp.report.view`; write `mrp.demand.write`. Gate every endpoint via `require_permission(...)` mirroring `app/api/v1/forecast.py`.
- **mrp-api tests:** `cd mrp-api && JWT_SECRET_KEY=test-secret TEST_PG_PASSWORD=<pw> ALLOWED_ORIGINS='["http://localhost:5179"]' python -m pytest tests -q`. Password: `docker inspect uniops_postgres --format '{{range .Config.Env}}{{println .}}{{end}}' | grep POSTGRES_PASSWORD`. Baseline before this plan: **121 passed, 0 skipped**. A "N skipped" DB result is a false pass.
- **Frontend typecheck:** `docker exec uniops_mrp_frontend sh -c 'cd /app && npx tsc -p tsconfig.app.json --noEmit'` — clean except the one pre-existing `baseUrl` TS5101 deprecation line.
- **Alembic:** new migration `down_revision` chains onto the real tail `mrp04_capacity_mps_demand`; single head; never `stamp`. Apply in-container (`docker exec uniops_mrp_api alembic upgrade head`), never from the host.
- **Branch:** work on `feature/mrp-phase0-foundations` (worktree `c:/Project/uniops-mrp-phase0`); commit per task; do NOT merge or push.
- **Reuse the grid response shape** `GridResponse = {months: list[str], rows: [{material_code, name, cells: {month: Decimal}, total}], column_totals: dict[str,Decimal], grand_total: Decimal}` (from `forecast.py`) verbatim for the series grid, so the frontend `MatrixGrid` wiring is unchanged.

---

## File Structure

**Backend (`mrp-api`)**
- `app/models/demand_series.py` — CREATE: `MrpDemandSeries`, `MrpForecastChangeLog`.
- `app/models/forecast.py` — MODIFY: add `source_anchor_month` to `ForecastVersion`.
- `alembic/versions/mrp05_demand_series.py` — CREATE: two new tables + the `source_anchor_month` column.
- `app/services/demand_series.py` — CREATE: read-grid + guarded upsert-with-change-log + outlook-freeze logic.
- `app/api/v1/series.py` — CREATE: `/series` + `/outlook` endpoints.
- `app/api/v1/__init__.py` — MODIFY: register the router.
- `app/api/v1/forecast.py` — MODIFY (Task 8): retire the per-version write/draft/confirm/import endpoints; keep version read + template/export.
- `tests/test_demand_series.py`, `tests/test_outlook.py` — CREATE.

**Frontend (`mrp/`)**
- `src/pages/forecast/seriesApi.ts` — CREATE: series client.
- `src/pages/forecast/SalesForecastPage.tsx` — CREATE: the rebuilt continuous-grid page (replaces `ForecastPage.tsx` as the route target).
- `src/pages/forecast/GenerateOutlookModal.tsx` — CREATE.
- `src/pages/forecast/CellHistoryPopover.tsx` — CREATE.
- `src/app/routes.tsx` — MODIFY (Task 7): point the Sales Forecast route at `SalesForecastPage`.
- `src/pages/forecast/ForecastPage.tsx`, `forecastApi.ts` — MODIFY/trim (Task 8): drop version-editing code now unused.

---

## Task 1: Continuous series + change-log models & migration

**Files:**
- Create: `mrp-api/app/models/demand_series.py`
- Modify: `mrp-api/app/models/forecast.py`
- Create: `mrp-api/alembic/versions/mrp05_demand_series.py`
- Test: covered by `tests/test_alembic_revisions.py` (single-head) — no new pytest here.

**Interfaces:**
- Produces: `MrpDemandSeries` (cols: id, material_code, month CHAR(7), qty Numeric, uom str default 'KG'; unique (material_code, month)); `MrpForecastChangeLog` (id, material_code, month, old_qty Numeric nullable, new_qty Numeric nullable, source str default 'manual', changed_by uuid nullable, changed_at timestamptz); `ForecastVersion.source_anchor_month: str | None`.

- [ ] **Step 1: Models.** `app/models/demand_series.py`:

```python
import uuid
from datetime import datetime
from sqlalchemy import CHAR, DateTime, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

class MrpDemandSeries(Base, UUIDPrimaryKey, TimestampMixin):
    """The single living demand table: one row per (finished good, absolute
    month). Unbounded in time, no version_id — the source of truth that
    outlook snapshots are frozen from (design §4.1). Sparse: a row exists
    only for a non-zero cell."""
    __tablename__ = "mrp_demand_series"
    __table_args__ = (UniqueConstraint("material_code", "month", name="uq_mrp_demand_series_material_month"),)
    material_code: Mapped[str] = mapped_column(String(50), index=True)
    month: Mapped[str] = mapped_column(CHAR(7), index=True)  # 'YYYY-MM'
    qty: Mapped[object] = mapped_column(Numeric(18, 3), default=0)
    uom: Mapped[str] = mapped_column(String(10), default="KG", server_default="KG")

class MrpForecastChangeLog(Base, UUIDPrimaryKey):
    """Append-only audit of every series cell edit (design §4.2): who/when/
    month/old->new. No TimestampMixin — this is immutable, it has its own
    changed_at and never updates."""
    __tablename__ = "mrp_forecast_change_log"
    material_code: Mapped[str] = mapped_column(String(50), index=True)
    month: Mapped[str] = mapped_column(CHAR(7), index=True)
    old_qty: Mapped[object | None] = mapped_column(Numeric(18, 3))
    new_qty: Mapped[object | None] = mapped_column(Numeric(18, 3))
    source: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")
    changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

In `app/models/forecast.py`, add to `ForecastVersion`:

```python
    source_anchor_month: Mapped[str | None] = mapped_column(CHAR(7))  # window anchor a snapshot was frozen from; NULL for legacy hand-built versions
```

- [ ] **Step 2: Migration** `alembic/versions/mrp05_demand_series.py`, `revision = "mrp05_demand_series"`, `down_revision = "mrp04_capacity_mps_demand"`. `upgrade()`: `create_table` for `mrp_demand_series` (uuid pk, timestamps, columns above, the unique constraint, indexes on material_code and month) and `mrp_forecast_change_log` (uuid pk, columns above, indexes), then `op.add_column("mrp_forecast_versions", sa.Column("source_anchor_month", sa.CHAR(length=7), nullable=True))`. `downgrade()`: drop the column, then the two tables (reverse order).

- [ ] **Step 3: Apply + verify single head.** `docker exec uniops_mrp_api alembic upgrade head`; `docker exec uniops_mrp_api alembic heads` shows exactly one (`mrp05_demand_series`); `pytest tests/test_alembic_revisions.py -q` PASS. Confirm tables: `docker exec uniops_postgres psql -U epms -d epms -c "\d mrp_demand_series" -c "\d mrp_forecast_change_log"`.

- [ ] **Step 4: Commit** `feat(mrp): continuous demand series + change-log tables`.

---

## Task 2: Series service — read grid, guarded upsert with change log

**Files:**
- Create: `mrp-api/app/services/demand_series.py`
- Test: `mrp-api/tests/test_demand_series.py`

**Interfaces:**
- Produces:
  - `generate_month_range(from_month: str, to_month: str) -> list[str]` — inclusive 'YYYY-MM' list.
  - `async read_series_grid(db, from_month, to_month) -> dict` — returns the `GridResponse` dict (`months`, `rows` with `name` resolved via `app.services.mdm_client.resolve_material_names`, `column_totals`, `grand_total`) over `mrp_demand_series`.
  - `async upsert_cells(db, cells: list[CellChange], current_month: str, changed_by: uuid.UUID | None, source: str = "manual") -> UpsertResult` where `CellChange` is a dataclass `(material_code, month, qty: Decimal, uom: str = "KG")`; writes `mrp_demand_series` and appends `mrp_forecast_change_log` for each cell whose value actually changed, in one transaction. Raises `PastMonthError` if any cell `month < current_month`; raises `UomError` if any `uom != "KG"`. `UpsertResult` = `(upserted: int, changed: int)`.

- [ ] **Step 1: Write failing tests** (`tests/test_demand_series.py`) — a new cell writes a series row + one change-log row (old=None,new=qty); editing it writes another log row (old,new) and updates the row; setting qty=0 deletes the row and logs (old,new=0); a no-op (same qty) writes nothing; a `month < current_month` raises `PastMonthError`; a non-KG cell raises `UomError`; `read_series_grid` returns the `GridResponse` shape with correct totals over a range. Example:

```python
import pytest
from decimal import Decimal
from app.services.demand_series import (
    upsert_cells, read_series_grid, CellChange, PastMonthError, UomError,
)

@pytest.mark.anyio
async def test_new_cell_writes_row_and_one_log(db_session):
    res = await upsert_cells(
        db_session, [CellChange("S0093", "2026-11", Decimal("100"))],
        current_month="2026-09", changed_by=None,
    )
    await db_session.commit()
    assert (res.upserted, res.changed) == (1, 1)
    grid = await read_series_grid(db_session, "2026-11", "2026-11")
    assert grid["rows"][0]["cells"]["2026-11"] == Decimal("100")
    # exactly one change-log row, old None -> new 100
    from app.models.demand_series import MrpForecastChangeLog
    from sqlalchemy import select
    logs = (await db_session.execute(select(MrpForecastChangeLog))).scalars().all()
    assert len(logs) == 1 and logs[0].old_qty is None and logs[0].new_qty == Decimal("100")

@pytest.mark.anyio
async def test_past_month_write_rejected(db_session):
    with pytest.raises(PastMonthError):
        await upsert_cells(db_session, [CellChange("S0093", "2026-08", Decimal("5"))],
                           current_month="2026-09", changed_by=None)

@pytest.mark.anyio
async def test_non_kg_rejected(db_session):
    with pytest.raises(UomError):
        await upsert_cells(db_session, [CellChange("S0093", "2026-11", Decimal("5"), uom="EA")],
                           current_month="2026-09", changed_by=None)
```

- [ ] **Step 2: Run → FAIL** (module missing).
- [ ] **Step 3: Implement `demand_series.py`.** Month helpers operate on 'YYYY-MM' strings (no date lib). `upsert_cells` first validates all cells (past-month, uom) before mutating so a bad cell rejects the whole batch atomically. For each cell it reads the existing row, computes old→new, and: inserts/updates (qty≠0) or deletes (qty==0) the series row, and appends a change-log row only when `old != new`. Uses `resolve_material_names` for `read_series_grid` names (batched, degrade-to-None on mdm-api failure — same contract as `forecast.py`).
- [ ] **Step 4: Run `pytest tests/test_demand_series.py -q` → PASS.**
- [ ] **Step 5: Commit** `feat(mrp): demand-series read + guarded upsert with change log`.

---

## Task 3: Series API endpoints

**Files:**
- Create: `mrp-api/app/api/v1/series.py`
- Modify: `mrp-api/app/api/v1/__init__.py`
- Test: `mrp-api/tests/test_demand_series.py` (add API cases)

**Interfaces:**
- Produces router `series.router` prefix `/series`:
  - `GET /series?from=YYYY-MM&to=YYYY-MM` → `GridResponse` (`mrp.report.view`).
  - `PUT /series/cells` `{cells:[{material_code,month,qty,uom?}]}` → `{upserted, changed}` (`mrp.demand.write`); 422 on past-month/non-KG (map `PastMonthError`/`UomError` to HTTP 422 with a readable detail); `current_month` resolved server-side from `datetime.now(timezone.utc)`.
  - `GET /series/change-log?material_code=&month=` → `{items:[{material_code,month,old_qty,new_qty,source,changed_by,changed_at}]}` newest-first (`mrp.report.view`); `month` optional (omit = all months for that material).

- [ ] **Step 1: Write failing API tests** — `PUT /series/cells` as admin upserts and returns counts; a past-month cell → 422; `GET /series` returns the grid; `GET /series/change-log` returns the edit history newest-first; a token without `mrp.demand.write` → 403 on PUT. Reuse `test_consignment.py`'s `client`/`admin_token`/`non_admin_token` fixtures.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `series.py`** (Pydantic request/response models; `try/except PastMonthError/UomError -> HTTPException 422`); register in `app/api/v1/__init__.py` (`from app.api.v1 import series` + `api_router.include_router(series.router)`).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(mrp): /series read/write/change-log API`.

---

## Task 4: Generate Outlook — freeze window into a snapshot version

**Files:**
- Modify: `mrp-api/app/services/demand_series.py` (add `freeze_outlook`), `mrp-api/app/api/v1/series.py` (add endpoint)
- Modify: `mrp-api/app/api/v1/forecast.py` (remove the supersede-prior-confirmed behaviour — see below)
- Test: `mrp-api/tests/test_outlook.py`

**Interfaces:**
- Produces: `async freeze_outlook(db, anchor_month: str, horizon_months: int, created_by) -> ForecastVersion` — copies the `[anchor, anchor+horizon)` slice of `mrp_demand_series` into a new `ForecastVersion` (`status="confirmed"`, `confirmed_at=now`, `horizon_start_month=anchor`, `horizon_months`, `source_anchor_month=anchor`, `version_no` = `FCV-<anchor>-<short-uid>`) plus its `ForecastLine` rows (one per non-zero series cell in range). `POST /series/outlook` `{anchor_month, horizon_months=18}` → `ForecastVersionResponse` (`mrp.demand.write`).

- [ ] **Step 1: Write failing tests** (`tests/test_outlook.py`): seed series cells across months; `freeze_outlook("2026-09", 18, ...)` creates a confirmed version whose lines exactly equal the non-zero series cells in `[2026-09, 2027-03)`; the version has `source_anchor_month == "2026-09"`; a second `freeze_outlook` after editing a series cell yields a NEW version while the first version's lines are unchanged (immutability); `POST /series/outlook` returns 201 with the version and it appears in `GET /forecast/versions` as `confirmed`. Also assert **freezing does NOT supersede** a prior confirmed version (both remain `confirmed`).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `freeze_outlook`** + the endpoint. Then in `forecast.py`'s `POST /versions/{id}/confirm` handler, **remove the block that sets other confirmed versions to `superseded`** (design §4.3: outlook snapshots coexist). Leave the confirm endpoint otherwise intact for now (Task 8 removes it). Generate `version_no` without `Date.now()` in a workflow sense — this is app code, `datetime.now(timezone.utc)` is fine; make `version_no` collision-safe by appending a short uid (`uuid4().hex[:6]`).
- [ ] **Step 4: Run `pytest tests/test_outlook.py -q` → PASS**, and `pytest tests/test_mps_api.py -q → PASS` (MPS still consumes a frozen version end-to-end).
- [ ] **Step 5: Commit** `feat(mrp): Generate Outlook freezes demand-series window into a snapshot`.

---

## Task 5: Seed the series from the existing confirmed version

**Files:**
- Modify: `mrp-api/alembic/versions/mrp05_demand_series.py` (data migration in `upgrade()` after table creation) OR a separate idempotent seed step — see Step 1.
- Test: manual verification (data migration).

- [ ] **Step 1:** Add to `mrp05`'s `upgrade()`, AFTER the `create_table`s, a data backfill that copies every `mrp_forecast_lines` row belonging to the single currently-`confirmed` `mrp_forecast_versions` into `mrp_demand_series` (`material_code`, `month`, `qty`, `uom='KG'`), skipping months already present (idempotent). Use `op.execute` with a SQL `INSERT ... SELECT ... ON CONFLICT (material_code, month) DO NOTHING`. This preserves the one real forecast as the living series' starting content. (Production has no real MRP data; dev has the one S0093 confirmed version.)
- [ ] **Step 2: Re-apply** the migration in-container (downgrade to `mrp04_capacity_mps_demand`, upgrade to head) and verify: `docker exec uniops_postgres psql -U epms -d epms -c "select material_code, month, qty from mrp_demand_series order by month"` shows the S0093 rows.
- [ ] **Step 3: Commit** `feat(mrp): seed demand series from the existing confirmed forecast`.

---

## Task 6: Frontend series client

**Files:**
- Create: `mrp/src/pages/forecast/seriesApi.ts`
- Test: `tsc` (no unit tests on the mrp frontend).

**Interfaces:**
- Produces `seriesApi`: `getGrid(from, to)`, `upsertCells(cells: Array<{material_code, month, qty}>)`, `getChangeLog(materialCode, month?)`, `generateOutlook(anchorMonth, horizonMonths=18)` against `/series` + `/series/outlook` via the mrp `api` client. Reuse the `GridResponse`/`GridRow` TS types from `forecastApi.ts` (re-export or import) since the shape is identical. Decimal fields (`qty`, `total`, cells) typed `string` on the wire, `Number()` at use.

- [ ] **Step 1:** Build `seriesApi.ts` mirroring `forecastApi.ts`'s client structure. `getChangeLog` returns `{items: Array<{material_code, month, old_qty: string|null, new_qty: string|null, source, changed_by, changed_at}>}`.
- [ ] **Step 2:** `tsc` clean.
- [ ] **Step 3: Commit** `feat(mrp): series API client`.

---

## Task 7: Rebuild the Sales Forecast page as a continuous grid

**Files:**
- Create: `mrp/src/pages/forecast/SalesForecastPage.tsx`, `GenerateOutlookModal.tsx`, `CellHistoryPopover.tsx`
- Modify: `mrp/src/app/routes.tsx` (point the Sales Forecast route at `SalesForecastPage`)
- Test: `tsc` + manual.

**Interfaces:**
- Consumes: `seriesApi` (Task 6), the existing `MatrixGrid` (direct edit + paste + add-row + focus-select), `materialsApi.listAll()` finished-goods list for the Add-Product picker.

- [ ] **Step 1:** `SalesForecastPage.tsx` — load `seriesApi.getGrid(rangeFrom, rangeTo)` with default range `[current_month − 3, current_month + 24]` (compute month strings client-side, no date lib beyond `new Date()` for the current month). Render via `MatrixGrid` (rows = materials; cols = the month range). **Past columns** (`month < current_month`) are passed as `frozenKeys` for every material so they render read-only; **highlight** the `[current_month, current_month+18)` window with a header band/marker. Commit edits via `seriesApi.upsertCells` (debounced autosave on cell commit; show a saved/saving indicator; three-state toasts, never silent). No version switcher / New / Save-Draft / Confirm.
- [ ] **Step 2:** Add-Product uses the finished-goods `MaterialPicker` (as `ForecastPage` did) to append a row; paste-to-add-rows keeps working via `MatrixGrid`'s `resolveMaterial`.
- [ ] **Step 3:** `GenerateOutlookModal.tsx` — anchor-month input (default current month) + horizon (default 18) → `seriesApi.generateOutlook`; on success toast "Outlook FCV-… generated" and a hint to run it in Production Plan. Button lives in the page toolbar, gated `mrp.demand.write`.
- [ ] **Step 4:** `CellHistoryPopover.tsx` — clicking a cell's history affordance opens a `createPortal` popover showing `seriesApi.getChangeLog(material, month)` newest-first ("2026-07-14: 50 → 60"). Keep it lightweight.
- [ ] **Step 5:** Point the Sales Forecast route in `routes.tsx` at `SalesForecastPage`. `tsc` clean. Commit `feat(mrp): continuous Sales Forecast page`.

---

## Task 8: Retire the per-version editing endpoints & dead frontend code

**Files:**
- Modify: `mrp-api/app/api/v1/forecast.py` (remove write/draft/confirm/import-write endpoints), its tests
- Modify: `mrp/src/pages/forecast/forecastApi.ts`, delete `ForecastPage.tsx`
- Test: full suites.

- [ ] **Step 1:** Remove now-unused endpoints from `forecast.py`: `POST /versions` (create/draft), `PUT /versions/{id}/cells`, `POST /versions/{id}/confirm`, `POST /versions/{id}/import`. **Keep** `GET /versions`, `GET /versions/{id}/grid` (read a frozen snapshot), `GET /versions/{id}/template`, `GET /versions/{id}/export` (still useful to view/export a snapshot). Update/remove the corresponding tests in `test_forecast.py` (delete the write/confirm/import cases; keep the read/export ones). Verify no other module imports the removed handlers.
- [ ] **Step 2:** In `forecastApi.ts`, remove `createVersion`, `upsertCells`, `confirmVersion`, `importForecast` and the now-unused types; keep `listVersions`, `getGrid`, `downloadTemplate`, `exportGrid`. Delete `ForecastPage.tsx` and any now-dead imports (`NewVersionModal.tsx`, `ImportWizard.tsx` if unused by the new page). Confirm `ProductionPlanPage`/`mpsApi` still compile (they only read versions).
- [ ] **Step 3:** Backend full suite → **must be ≥ 121 + new tests, 0 skipped**. `tsc` clean.
- [ ] **Step 4: Commit** `refactor(mrp): retire per-version forecast editing (moved to continuous series)`.

---

## Self-Review (coverage vs spec)

- §4.1 `mrp_demand_series` → T1; §4.2 `mrp_forecast_change_log` → T1/T2 (written in upsert); §4.3 version reuse + `source_anchor_month` + drop-supersede → T1/T4.
- §5 continuous grid, month range, past read-only, highlight, no draft/confirm → T7; change-history popover → T7; edits write series + change log → T2/T3/T7.
- §6 Generate Outlook → snapshot → MPS unchanged → T4 (+ T4 asserts MPS still runs).
- §7 endpoints: `GET /series`, `PUT /series/cells`, `GET /series/change-log` → T3; `POST /outlook` → T4; retire per-version write endpoints → T8.
- §8 migration + seed → T1/T5; Phase 1B untouched → verified in T4/T8 (mps suite green).
- §9 out of scope (actuals, carry-forward, compare view) → no task, correctly absent.
- §10 testing → T2/T3/T4 cover series/change-log/outlook; full-suite green gate in T8.
- **Type consistency:** `CellChange(material_code, month, qty, uom)`, `GridResponse` shape, `freeze_outlook(...) -> ForecastVersion`, `source_anchor_month` used consistently T1→T8.
- §11 open questions: autosave-on-blur chosen (T7 Step 1); default range `[−3, +24]` chosen (T7); change-history popover ships this phase (T7 Step 4).
