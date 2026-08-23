# MRP Phase 1B — MPS Capacity Balancing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn monthly net requirements into a capacity-feasible, shelf-life-valid production plan (MPS) that the planner can adjust, lock, recalculate, and release as the MRP input (`mrp_demands`).

**Architecture:** New capacity-rule master + MPS run/line tables in `mrp-api` (own `alembic_version_mrp` chain). A **pure** scheduling function (`mps_engine.py`, no DB/IO) loads each month's net demand against effective capacity rules, forward-shifts (pre-builds) overflow subject to a shelf-life hard check, and emits capacity-gap exceptions; a thin API layer wraps it (generate / get / recalculate / manual-adjust / lock / confirm-release). Confirm-release materializes the plan into `mrp_demands`. Three React pages in the existing `mrp/` app (Capacity Rules, Production Plan/MPS) consume it. Net-requirement opening stock gets a KG-invariant guard first (Task 0) so capacity planning never sums mismatched units.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (mrp-api, Python 3.12, thick oracledb already wired); React 19 + TS + Tailwind + `@uniops/shell` (mrp app, Vite 5179); Pydantic v2; pytest.

## Global Constraints

- **Money/quantity = Decimal end to end.** Never float in any quantity/capacity math. Pydantic serializes Decimal as a JSON string; the frontend must `Number()` before arithmetic (see `feedback_uniops_decimal_as_string`).
- **Planning UOM is KG.** All finished-goods quantities (forecast, WMS, consignment, MPS, capacity tonnage) are kilograms. Tonnes = kg / 1000. Enforce, never assume (Task 0).
- **User-facing copy is English only.** Comments may be Chinese.
- **mrp-api tests** run from `cd mrp-api` with `JWT_SECRET_KEY=test-secret TEST_PG_PASSWORD=<uniops_postgres POSTGRES_PASSWORD> ALLOWED_ORIGINS='["http://localhost:5179"]' python -m pytest tests -q`. Password: `docker inspect uniops_postgres --format '{{range .Config.Env}}{{println .}}{{end}}' | grep POSTGRES_PASSWORD`. Baseline before this plan: **95 passed**.
- **Frontend typecheck:** `docker exec uniops_mrp_frontend sh -c 'cd /app && npx tsc -p tsconfig.app.json --noEmit'` — clean except the pre-existing `baseUrl` TS5101 deprecation line (ignore only that one).
- **Alembic:** new migrations `down_revision` must chain onto the real tail `mrp02_forecast_consignment`; keep a single head (`alembic heads` shows one). Never `stamp`.
- **Permissions (already seeded in Phase 0, do not re-add):** view `mrp.report.view`; capacity edit `mrp.param.write`; MPS generate/recalc `mrp.run.execute`; confirm & release `mrp.proposal.confirm`. Gate every endpoint via `require_permission(...)` (mirror `app/api/v1/consignment.py`).
- **Never block on WMS/NC being down** where a live call is involved (design §6.3 pattern) — but Phase 1B has no new live WMS calls; it reads local tables only.
- **Branch:** keep working on `feature/mrp-phase0-foundations` (worktree `c:/Project/uniops-mrp-phase0`); commit per task; do NOT merge to main or push (user holds integration until the whole feature is done).

---

## File Structure

**Backend (`mrp-api`)**
- `app/services/net_requirement.py` — MODIFY: KG-invariant guard on opening-stock sum (Task 0).
- `app/models/consignment.py` — MODIFY: add `uom` column (Task 0).
- `alembic/versions/mrp03_consignment_uom.py` — CREATE: add `mrp_consignment_stock.uom` (Task 0).
- `app/models/capacity.py` — CREATE: `MrpCapacityRule` (Task 1).
- `app/models/mps.py` — CREATE: `MrpMpsRun`, `MrpMpsLine` (Task 2).
- `app/models/demand.py` — CREATE: `MrpDemand` (release target, Task 2).
- `alembic/versions/mrp04_capacity_mps_demand.py` — CREATE: the four new tables (Task 1+2 share one migration; written in Task 1, extended in Task 2).
- `app/services/capacity.py` — CREATE: effective-rule resolution (Task 1).
- `app/services/mps_engine.py` — CREATE: the **pure** scheduling algorithm (Task 3).
- `app/api/v1/capacity.py` — CREATE: capacity-rule CRUD (Task 1).
- `app/api/v1/mps.py` — CREATE: MPS run endpoints (Task 4).
- `app/api/v1/__init__.py` — MODIFY: register the two routers (Task 1, Task 4).
- `tests/test_net_requirement.py` — MODIFY: KG-guard cases (Task 0).
- `tests/test_capacity.py`, `tests/test_mps_engine.py`, `tests/test_mps_api.py` — CREATE.

**Frontend (`mrp/`)**
- `src/pages/capacity/capacityApi.ts`, `CapacityRulesPage.tsx`, `RuleDrawer.tsx` — CREATE (Task 5).
- `src/pages/mps/mpsApi.ts`, `ProductionPlanPage.tsx`, `CapacityBars.tsx`, `MpsLineTable.tsx`, `AdjustDrawer.tsx` — CREATE (Task 6).
- `src/components/layout/AppLayout.tsx` — MODIFY: add nav entries (Task 7).
- `src/app/routes.tsx` — MODIFY: register routes (Task 7).

---

## Task 0: KG-invariant guard on opening stock

Closes the design's blocking gap (`net_requirement.py`'s `TODO(phase-1b)`): WMS + consignment quantities are summed with no UOM check. Decision (2026-08-06): enforce the KG invariant rather than build a conversion engine (finished goods are all KG). Add a `uom` column to consignment entries (default `KG`), and make the opening-stock breakdown reject a sum where any source's unit is not the planning UOM.

**Files:**
- Modify: `mrp-api/app/models/consignment.py`
- Create: `mrp-api/alembic/versions/mrp03_consignment_uom.py`
- Modify: `mrp-api/app/services/net_requirement.py`
- Test: `mrp-api/tests/test_net_requirement.py`

**Interfaces:**
- Produces: `PLANNING_UOM = "KG"` (module constant in `net_requirement.py`); `OpeningStockBreakdown.opening_stock` returns `Decimal` and raises `UomMismatchError` (new, in `net_requirement.py`) when a contributing source is non-KG. `ConsignmentStock.uom: str` column, default `"KG"`.

- [ ] **Step 1: Write the failing migration-presence + column test**

Add to `tests/test_net_requirement.py`:

```python
from decimal import Decimal
import pytest
from app.services.net_requirement import (
    OpeningStockBreakdown, UomMismatchError, PLANNING_UOM,
)

def test_planning_uom_is_kg():
    assert PLANNING_UOM == "KG"

def test_opening_stock_sums_when_all_kg():
    b = OpeningStockBreakdown(
        wms_qty=Decimal("100"), wms_uom="KG",
        consignment_qty=Decimal("40"), consignment_uom="KG",
        consignment_count_date=None, wms_synced_at=None,
    )
    assert b.opening_stock == Decimal("140")

def test_opening_stock_rejects_non_kg_source():
    b = OpeningStockBreakdown(
        wms_qty=Decimal("100"), wms_uom="KG",
        consignment_qty=Decimal("40"), consignment_uom="EA",
        consignment_count_date=None, wms_synced_at=None,
    )
    with pytest.raises(UomMismatchError):
        _ = b.opening_stock
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd mrp-api && ... python -m pytest tests/test_net_requirement.py -q`
Expected: FAIL (`ImportError: UomMismatchError` / unexpected kwargs).

- [ ] **Step 3: Add the `uom` column + migration**

In `app/models/consignment.py` add after `qty`:

```python
    uom: Mapped[str] = mapped_column(String(10), default="KG", server_default="KG")
```

Create `alembic/versions/mrp03_consignment_uom.py`:

```python
"""add uom to mrp_consignment_stock

Revision ID: mrp03_consignment_uom
Revises: mrp02_forecast_consignment
"""
from alembic import op
import sqlalchemy as sa

revision = "mrp03_consignment_uom"
down_revision = "mrp02_forecast_consignment"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column(
        "mrp_consignment_stock",
        sa.Column("uom", sa.String(length=10), nullable=False, server_default="KG"),
    )

def downgrade() -> None:
    op.drop_column("mrp_consignment_stock", "uom")
```

- [ ] **Step 4: Implement the guard in `net_requirement.py`**

Replace the `OpeningStockBreakdown` dataclass + its `opening_stock` property:

```python
PLANNING_UOM = "KG"

class UomMismatchError(ValueError):
    """A contributing stock source is not in the planning UOM (KG) and no
    conversion exists — summing it would silently produce a wrong net
    requirement (design decision 2026-08-06: enforce, don't convert)."""

@dataclass(frozen=True)
class OpeningStockBreakdown:
    wms_qty: Decimal
    wms_uom: str
    consignment_qty: Decimal
    consignment_uom: str
    consignment_count_date: date | None
    wms_synced_at: datetime | None

    @property
    def opening_stock(self) -> Decimal:
        for label, qty, uom in (
            ("wms", self.wms_qty, self.wms_uom),
            ("consignment", self.consignment_qty, self.consignment_uom),
        ):
            if qty and uom != PLANNING_UOM:
                raise UomMismatchError(
                    f"{label} stock is in {uom!r}, not {PLANNING_UOM!r}; refusing to sum"
                )
        return self.wms_qty + self.consignment_qty
```

Update `get_opening_stock_breakdown` to pass `wms_uom=PLANNING_UOM` (the WMS mirror has no unit column; finished-goods WMS is KG by survey — assert the invariant here rather than read a column that doesn't exist) and `consignment_uom` = the latest count's `uom` (add it to `_consignment_latest_qty`'s select; when rows disagree, take any since they're one warehouse/material — a follow-up guard can flag mixed units, out of scope here). Delete the old `TODO(phase-1b)` comment.

- [ ] **Step 5: Run tests to verify pass**

Run: `cd mrp-api && ... python -m pytest tests/test_net_requirement.py -q`
Expected: PASS. Then run the DB migration against the dev container so downstream tasks see the column: `docker exec uniops_mrp_api alembic upgrade head`.

- [ ] **Step 6: Commit**

```bash
git add mrp-api/app/models/consignment.py mrp-api/alembic/versions/mrp03_consignment_uom.py mrp-api/app/services/net_requirement.py mrp-api/tests/test_net_requirement.py
git commit -m "feat(mrp): enforce KG planning-UOM invariant on opening stock"
```

---

## Task 1: Capacity rules — model, migration, CRUD API

`mrp_capacity_rules` makes capacity user-definable (design §6.4): a rule is `scope_type` (factory/product_family/line) × `constraint_type` (max_sku_count / max_output_qty) × `limit_value` × effective window. Phase 1B only needs factory-level rules but the schema is general.

**Files:**
- Create: `mrp-api/app/models/capacity.py`
- Create: `mrp-api/alembic/versions/mrp04_capacity_mps_demand.py` (this migration; Task 2 extends it)
- Create: `mrp-api/app/services/capacity.py`
- Create: `mrp-api/app/api/v1/capacity.py`
- Modify: `mrp-api/app/api/v1/__init__.py`
- Test: `mrp-api/tests/test_capacity.py`

**Interfaces:**
- Produces: `MrpCapacityRule` model; `resolve_effective_rules(db, on_month: str) -> list[MrpCapacityRule]` in `capacity.py`; router `capacity.router` at `/capacity/rules` (GET list, POST create, PATCH `{id}`, DELETE `{id}`), all `mrp.param.write` except GET (`mrp.report.view`).

- [ ] **Step 1: Write the failing service test**

`tests/test_capacity.py`:

```python
import pytest
from datetime import date
from app.services.capacity import resolve_effective_rules
from app.models.capacity import MrpCapacityRule

@pytest.mark.anyio
async def test_resolve_effective_rules_filters_by_active_and_window(db_session):
    db_session.add_all([
        MrpCapacityRule(scope_type="factory", scope_ref=None, constraint_type="max_sku_count",
                        limit_value=12, uom=None, effective_from=date(2026,1,1),
                        effective_to=None, is_active=True),
        MrpCapacityRule(scope_type="factory", scope_ref=None, constraint_type="max_output_qty",
                        limit_value=160000, uom="KG", effective_from=date(2026,1,1),
                        effective_to=date(2026,6,30), is_active=True),  # expired for 2026-09
        MrpCapacityRule(scope_type="factory", scope_ref=None, constraint_type="max_sku_count",
                        limit_value=99, uom=None, effective_from=date(2026,1,1),
                        effective_to=None, is_active=False),  # inactive
    ])
    await db_session.commit()
    rules = await resolve_effective_rules(db_session, "2026-09")
    kinds = {(r.constraint_type, r.limit_value) for r in rules}
    assert kinds == {("max_sku_count", 12)}
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_capacity.py -q` → FAIL (module missing).

- [ ] **Step 3: Model + migration**

`app/models/capacity.py`:

```python
from datetime import date
from sqlalchemy import Boolean, Date, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

class MrpCapacityRule(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_capacity_rules"
    scope_type: Mapped[str] = mapped_column(String(20))          # factory|product_family|line
    scope_ref: Mapped[str | None] = mapped_column(String(50))    # null for factory-wide
    constraint_type: Mapped[str] = mapped_column(String(20))     # max_sku_count|max_output_qty
    limit_value: Mapped[object] = mapped_column(Numeric(18, 3))
    uom: Mapped[str | None] = mapped_column(String(10))          # KG for max_output_qty; null for counts
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
```

Create `alembic/versions/mrp04_capacity_mps_demand.py` with `down_revision = "mrp03_consignment_uom"` creating `mrp_capacity_rules` (id uuid pk, timestamps, the columns above). (Task 2 adds three more `op.create_table` calls to this same migration.)

- [ ] **Step 4: Service**

`app/services/capacity.py`:

```python
from datetime import date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.capacity import MrpCapacityRule

def _month_first_day(month: str) -> date:
    y, m = month.split("-")
    return date(int(y), int(m), 1)

async def resolve_effective_rules(db: AsyncSession, on_month: str) -> list[MrpCapacityRule]:
    """Active rules whose [effective_from, effective_to] window covers the
    first day of `on_month` ('YYYY-MM'). effective_to NULL = open-ended."""
    d = _month_first_day(on_month)
    rows = (await db.execute(select(MrpCapacityRule).where(MrpCapacityRule.is_active.is_(True)))).scalars().all()
    return [r for r in rows if r.effective_from <= d and (r.effective_to is None or r.effective_to >= d)]
```

- [ ] **Step 5: Run service test → PASS.**

- [ ] **Step 6: CRUD API + register router**

`app/api/v1/capacity.py`: Pydantic `CapacityRuleCreate/Update/Response`, router `prefix="/capacity"`, endpoints `GET /rules` (`mrp.report.view`), `POST /rules` / `PATCH /rules/{id}` / `DELETE /rules/{id}` (`mrp.param.write`). Mirror `consignment.py`'s `require_permission` deps and 404 helper. Register in `app/api/v1/__init__.py`: `from app.api.v1 import capacity` + `api_router.include_router(capacity.router)`.

Add `tests/test_capacity.py::test_crud_roundtrip_and_permission_gate` (create as admin → 201; list contains it; PATCH `is_active=False` → 200; a token without `mrp.param.write` → 403 on POST). Reuse `test_consignment.py`'s `client`/`admin_token` fixtures pattern.

- [ ] **Step 7: Run `pytest tests/test_capacity.py -q` → PASS. Commit.**

```bash
git add mrp-api/app/models/capacity.py mrp-api/app/services/capacity.py mrp-api/app/api/v1/capacity.py mrp-api/app/api/v1/__init__.py mrp-api/alembic/versions/mrp04_capacity_mps_demand.py mrp-api/tests/test_capacity.py
git commit -m "feat(mrp): capacity rules master + CRUD API"
```

---

## Task 2: MPS run/line + demand tables

**Files:**
- Create: `mrp-api/app/models/mps.py`, `mrp-api/app/models/demand.py`
- Modify: `mrp-api/alembic/versions/mrp04_capacity_mps_demand.py` (add three tables)
- Test: `mrp-api/tests/test_capacity.py` (migration-applies smoke via existing `test_alembic_revisions.py` — see step 3)

**Interfaces:**
- Produces: `MrpMpsRun`, `MrpMpsLine`, `MrpDemand` models with the fields below (Task 4 & the algorithm consume them).

- [ ] **Step 1: Models**

`app/models/mps.py`:

```python
import uuid
from datetime import datetime
from sqlalchemy import Boolean, CHAR, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

class MrpMpsRun(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_mps_runs"
    run_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    forecast_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    horizon_start_month: Mapped[str] = mapped_column(CHAR(7))
    horizon_months: Mapped[int] = mapped_column(default=18)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")  # draft|confirmed|released
    safety_margin_fraction: Mapped[object] = mapped_column(Numeric(6, 4), default=0)  # shelf-life pre-build safety, e.g. 0.3333
    generated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stats: Mapped[dict | None] = mapped_column(JSONB)

class MrpMpsLine(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_mps_lines"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mrp_mps_runs.id", ondelete="CASCADE"), index=True)
    material_code: Mapped[str] = mapped_column(String(50), index=True)
    demand_month: Mapped[str] = mapped_column(CHAR(7))
    plan_month: Mapped[str] = mapped_column(CHAR(7))
    qty: Mapped[object] = mapped_column(Numeric(18, 3))
    is_prebuild: Mapped[bool] = mapped_column(Boolean, default=False)
    prebuild_reason: Mapped[str | None] = mapped_column(Text)
    shelf_life_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    capacity_gap: Mapped[bool] = mapped_column(Boolean, default=False)  # couldn't place -> exception
    locked_by_planner: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_adjusted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
```

`app/models/demand.py`:

```python
import uuid
from sqlalchemy import CHAR, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

class MrpDemand(Base, UUIDPrimaryKey, TimestampMixin):
    """MRP input, materialized from a released MPS run (design §6.4: mrp_demands
    is no longer hand-imported). Phase 1C's material explosion reads this."""
    __tablename__ = "mrp_demands"
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    demand_type: Mapped[str] = mapped_column(String(20), default="mps", server_default="mps")
    material_code: Mapped[str] = mapped_column(String(50), index=True)
    demand_month: Mapped[str] = mapped_column(CHAR(7), index=True)
    qty: Mapped[object] = mapped_column(Numeric(18, 3))
```

- [ ] **Step 2: Extend migration `mrp04`** with `op.create_table` for `mrp_mps_runs`, `mrp_mps_lines` (FK to runs, `ondelete=CASCADE`), `mrp_demands`, matching the models. Keep all four tables in this one migration.

- [ ] **Step 3: Apply + verify single head**

Run: `docker exec uniops_mrp_api alembic upgrade head` then `docker exec uniops_mrp_api alembic heads` (expect exactly one: `mrp04_capacity_mps_demand`). The repo's `tests/test_alembic_revisions.py` already asserts a single head — run `pytest tests/test_alembic_revisions.py -q` → PASS.

- [ ] **Step 4: Commit**

```bash
git add mrp-api/app/models/mps.py mrp-api/app/models/demand.py mrp-api/alembic/versions/mrp04_capacity_mps_demand.py
git commit -m "feat(mrp): MPS run/line + mrp_demands tables"
```

---

## Task 3: MPS scheduling algorithm (pure)

The core. A pure function — no DB, no clock — so it is exhaustively unit-testable (design §6.5). Loads each demand month against capacity, forward-shifts overflow as pre-build subject to a shelf-life hard check, and marks unplaceable demand as a capacity gap.

**Files:**
- Create: `mrp-api/app/services/mps_engine.py`
- Test: `mrp-api/tests/test_mps_engine.py`

**Interfaces:**
- Consumes: net requirements as `list[DemandItem]`, capacity as `CapacityLimits`, shelf life as `dict[str,int|None]`, locked lines as `list[PlannedLine]`.
- Produces:

```python
@dataclass(frozen=True)
class DemandItem:
    material_code: str
    demand_month: str        # 'YYYY-MM'
    qty: Decimal

@dataclass(frozen=True)
class CapacityLimits:
    max_sku_count: int | None      # per month; None = unlimited
    max_output_qty: Decimal | None # per month, KG; None = unlimited

@dataclass(frozen=True)
class PlannedLine:
    material_code: str
    demand_month: str
    plan_month: str
    qty: Decimal
    is_prebuild: bool
    prebuild_reason: str | None
    shelf_life_ok: bool
    capacity_gap: bool
    locked: bool

def generate_mps(
    demands: list[DemandItem],
    limits: CapacityLimits,
    shelf_life_months: dict[str, int | None],
    safety_margin_fraction: Decimal,
    locked: list[PlannedLine] | None = None,
) -> list[PlannedLine]: ...
```

**Algorithm (design §6.5), implement exactly:**
1. Seed each month's load with any `locked` lines placed in that `plan_month` (their SKUs and qty count against capacity and never move).
2. Iterate demand months ascending. Place each demand in its own month if capacity allows (SKU-count and output-qty both under limit after adding).
3. On overflow, pick movable products (not locked) by **shelf-life headroom descending, then qty descending** and pre-build them into the nearest earlier month with room, checked each hop: (a) target month has SKU + qty room; (b) shelf-life hard check `prebuild_months <= floor(shelf_life * (1 - safety_margin_fraction))` where `prebuild_months` = months between plan and demand. A product with unknown shelf life (`None`) may not be pre-built (fail safe).
4. If it cannot be placed anywhere, emit a line with `capacity_gap=True`, `plan_month == demand_month`, `shelf_life_ok` per the check — never silently drop it.
5. `is_prebuild = plan_month != demand_month`; `prebuild_reason` names the constraint hit (e.g. `"2026-11 over max_output_qty 160000 KG"`).

- [ ] **Step 1: Write failing tests** covering: no-op when under capacity; SKU-count overflow forces a pre-build to the prior month; output-qty overflow; shelf-life blocks a too-early pre-build (→ capacity_gap); unknown shelf life is never pre-built; a locked line is untouched and still consumes capacity. Example:

```python
from decimal import Decimal
from app.services.mps_engine import (
    DemandItem, CapacityLimits, generate_mps,
)

def test_under_capacity_places_in_demand_month():
    out = generate_mps(
        [DemandItem("S0060", "2026-11", Decimal("100"))],
        CapacityLimits(max_sku_count=12, max_output_qty=Decimal("160000")),
        {"S0060": 18}, Decimal("0.3333"),
    )
    assert len(out) == 1
    line = out[0]
    assert (line.plan_month, line.is_prebuild, line.capacity_gap) == ("2026-11", False, False)

def test_output_overflow_prebuilds_to_prior_month():
    demands = [
        DemandItem("A", "2026-11", Decimal("120000")),
        DemandItem("B", "2026-11", Decimal("120000")),
    ]
    out = generate_mps(
        demands, CapacityLimits(max_sku_count=12, max_output_qty=Decimal("160000")),
        {"A": 18, "B": 18}, Decimal("0.3333"),
    )
    by_code = {l.material_code: l for l in out}
    # 240k demanded in one month, 160k/mo cap -> one product pre-built earlier
    assert any(l.is_prebuild and l.plan_month == "2026-10" for l in out)
    assert sum(l.qty for l in out) == Decimal("240000")
    assert not any(l.capacity_gap for l in out)

def test_shelf_life_blocks_too_early_prebuild_becomes_gap():
    # 3 months of full demand, 1-month shelf life (minus safety) forbids
    # pre-building more than ~0 months ahead -> the overflow can't move.
    demands = [DemandItem(c, "2026-11", Decimal("160000")) for c in ("A", "B")]
    out = generate_mps(
        demands, CapacityLimits(max_sku_count=12, max_output_qty=Decimal("160000")),
        {"A": 1, "B": 1}, Decimal("0.3333"),
    )
    assert any(l.capacity_gap for l in out)

def test_unknown_shelf_life_is_never_prebuilt():
    demands = [DemandItem(c, "2026-11", Decimal("160000")) for c in ("A", "B")]
    out = generate_mps(
        demands, CapacityLimits(max_sku_count=12, max_output_qty=Decimal("160000")),
        {"A": None, "B": None}, Decimal("0.3333"),
    )
    assert all(not l.is_prebuild for l in out)
    assert any(l.capacity_gap for l in out)
```

- [ ] **Step 2: Run → FAIL (module missing).**
- [ ] **Step 3: Implement `mps_engine.py`** per the algorithm above (Decimal throughout; months compared and decremented via a small `YYYY-MM` helper — add `_month_index`/`_shift_month` local helpers, do not import a date lib for month strings).
- [ ] **Step 4: Run `pytest tests/test_mps_engine.py -q` → PASS (all cases).**
- [ ] **Step 5: Commit** `feat(mrp): pure MPS scheduling algorithm (pre-build + shelf-life)`.

---

## Task 4: MPS run API

Wires the pure engine to data: pull the confirmed forecast → net requirements (reuse Task 1A's `compute_net_requirements` + Task 0's guarded opening stock) → effective capacity rules → `generate_mps` → persist run + lines. Plus get / recalculate / manual-adjust / lock / confirm-release.

**Files:**
- Create: `mrp-api/app/api/v1/mps.py`; Modify: `app/api/v1/__init__.py`; Test: `tests/test_mps_api.py`.

**Interfaces:**
- Produces router `mps.router` at `/mps`:
  - `POST /runs` `{forecast_version_id, safety_margin_fraction?}` → generates, persists, returns run + lines. `mrp.run.execute`.
  - `GET /runs/{id}` → run + lines + per-month capacity occupancy. `mrp.report.view`.
  - `POST /runs/{id}/recalculate` → re-run keeping `locked_by_planner` lines fixed. `mrp.run.execute`.
  - `PATCH /runs/{id}/lines/{line_id}` `{qty?, plan_month?, locked_by_planner?}` (sets `manual_adjusted`). `mrp.run.execute`.
  - `POST /runs/{id}/confirm-release` → status `released`, delete prior `mrp_demands` where `source_run_id` for this forecast lineage, insert one `MrpDemand` per line (qty in KG, `demand_month = plan_month`). `mrp.proposal.confirm`.

- [ ] **Step 1: Write failing API test** (`tests/test_mps_api.py`): seed a confirmed forecast version + lines (reuse forecast fixtures) and a factory capacity rule; `POST /mps/runs` → 201 with lines; `POST /confirm-release` → 200 and `mrp_demands` now holds one row per released line with `demand_month == plan_month`; a token lacking `mrp.proposal.confirm` → 403 on release. Assert generate uses net requirements (a line whose opening stock ≥ forecast produces no MPS line / zero qty).

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `mps.py`.** Default `safety_margin_fraction` = `Decimal("0.3333")` when omitted (design §6.8 "保质期的 1/3"); store on the run. Load shelf life from mdm materials (`materials.shelf_life_months`) via `app/services/mdm_client.py` (add a `fetch_shelf_life(codes)` helper mirroring `resolve_material_names`); a missing value → `None` → never pre-built. `run_no` via max-tail+1 under an advisory lock (reuse the document-number idiom already in the repo — see `feedback` on collisions; if no shared helper exists in mrp-api, generate `MPS-YYYYMMDD-####` scanning existing `run_no`). Capacity occupancy in `GET` is computed, not stored.
- [ ] **Step 4: Run `pytest tests/test_mps_api.py -q` → PASS.**
- [ ] **Step 5: Commit** `feat(mrp): MPS run/generate/recalculate/adjust/release API`.

---

## Task 5: Capacity Rules page (frontend)

Design §6.6 page 2: list + drawer form; progressive fields by `constraint_type` (count rules hide the UOM); overlapping windows warn inline, don't block.

**Files:**
- Create: `mrp/src/pages/capacity/capacityApi.ts`, `CapacityRulesPage.tsx`, `RuleDrawer.tsx`
- Modify (Task 7 wires nav/route)

**Interfaces:**
- `capacityApi`: `list()`, `create(body)`, `update(id, body)`, `remove(id)` against `/capacity/rules` via the mrp `api` client (mirror `consignmentApi.ts`). Decimal `limit_value` typed as `string` on the wire, `Number()` at use.

- [ ] **Step 1** Build `capacityApi.ts` (types + client). No test framework on the mrp frontend — the gate is `tsc` + manual verify.
- [ ] **Step 2** `CapacityRulesPage.tsx`: table (Scope / Constraint / Limit / Unit / Effective / Status), `PortalChromeLayout`-style page wrapper matching `ConsignmentStockPage`, `StatusBadge` for active/inactive, toolbar "New rule" opening `RuleDrawer`. Gate the page behind `mrp.param.write` via `usePermissions()` (view-only if only `mrp.report.view`).
- [ ] **Step 3** `RuleDrawer.tsx`: form with `scope_type`, conditional `scope_ref`, `constraint_type` → conditionally show `uom` only for `max_output_qty`; `limit_value`; effective dates; onBlur validation with errors beside fields (`role="alert"`); inline warning when the new window overlaps an existing active rule of the same scope+constraint. Submit → loading→toast (three-state), never silent.
- [ ] **Step 4** `tsc` clean; commit `feat(mrp): Capacity Rules page`.

---

## Task 6: Production Plan / MPS page (frontend)

Design §6.6 page 3 — the most valuable screen. Capacity bars per month (occupancy, full/over in colour + text), plan-line table with pre-build `^ n mo`, shelf-life column, lock/adjust/recalc, and `Confirm & Release` with a summary confirm.

**Files:**
- Create: `mrp/src/pages/mps/mpsApi.ts`, `ProductionPlanPage.tsx`, `CapacityBars.tsx`, `MpsLineTable.tsx`, `AdjustDrawer.tsx`

**Interfaces:**
- `mpsApi`: `generate(forecastVersionId, safetyMarginFraction?)`, `get(runId)`, `recalculate(runId)`, `adjustLine(runId, lineId, body)`, `confirmRelease(runId)`.

- [ ] **Step 1** `mpsApi.ts` types + client (Decimal-as-string).
- [ ] **Step 2** `CapacityBars.tsx`: one bar per horizon month showing `used/limit t` and `n/max SKUs`, full at 100% and over-limit rendered with colour **and** text (never colour alone).
- [ ] **Step 3** `MpsLineTable.tsx`: columns Product / Demand Mon / Plan Mon / Qty / Pre-build (`^ n mo` + hover reason) / Shelf (OK/GAP) / Status (Locked/Blocked). Checkbox column + top action bar (Lock selected / Unlock / Adjust… / Confirm & Release). Capacity-gap rows styled as blocked with readable reason; feed them into an exception summary line.
- [ ] **Step 4** `AdjustDrawer.tsx`: edit qty or plan_month for one line → `adjustLine` → refetch; `ProductionPlanPage.tsx` wires forecast-version picker (confirmed version), Generate/Recalculate, the bars, the table, and a `Confirm & Release` that first shows a summary (product count, total qty, # pre-built, # gaps) then calls `confirmRelease`. Gate Generate/Recalc behind `mrp.run.execute`, Release behind `mrp.proposal.confirm`.
- [ ] **Step 5** `tsc` clean; commit `feat(mrp): Production Plan / MPS page`.

---

## Task 7: Navigation, routes, permissions, smoke

**Files:**
- Modify: `mrp/src/components/layout/AppLayout.tsx` (nav entries: "Capacity Rules" `/capacity-rules` gated `mrp.param.write`; "Production Plan" `/production-plan` gated `mrp.run.execute`), `mrp/src/app/routes.tsx` (register both routes + the tab metadata like the existing pages).

- [ ] **Step 1** Add nav items + routes following the existing `Consignment Stock` / `BOM Explorer` entries exactly (icon from `lucide-react`, `href`, `key/title/path` in routes).
- [ ] **Step 2** `tsc` clean.
- [ ] **Step 3** Manual smoke (E2E is blocked by the safety classifier — hand to the user): log in at 5179, create a factory `max_sku_count=12` + `max_output_qty=160000 KG` rule; on Production Plan pick the confirmed forecast, Generate, confirm the capacity bars + any pre-build/gap read correctly, then Confirm & Release and verify `mrp_demands` populated (`docker exec uniops_postgres psql -U epms -d epms -c "select count(*) from mrp_demands"`).
- [ ] **Step 4** Commit `feat(mrp): wire MPS + Capacity nav/routes`.

---

## Self-Review Notes (coverage vs spec §6.4–6.7)

- §6.4 tables: `mrp_capacity_rules` (T1), `mrp_mps_runs`/`mrp_mps_lines` (T2), `mrp_demands` release target (T2). `mrp_actual_output` is **Phase 1C** (actual-output backfill / attainment), intentionally out of this plan.
- §6.5 algorithm: T3 (pure) + T4 (wiring), including locked lines, pre-build, shelf-life hard check, capacity-gap exceptions (never silently dropped).
- §6.6 pages 2 & 3: T5, T6. Page 5 (Actual Output) is Phase 1C.
- §6.7 exit criterion ("15 SKUs / 300t squeezed into 12 SKUs / 160t, overflow moved forward within shelf life, gaps as exceptions"): exercised by T3 unit tests + T7 manual smoke.
- §6.8 open params: shelf-life safety margin defaults to 1/3 on the run (T4), overridable per generate; capacity dimensions beyond factory are schema-supported (T1 `scope_type`) but only factory rules are needed now.
- **Blocker resolved:** UOM (T0) — KG invariant guard, per 2026-08-06 decision.
- **Loss rate** (raw/packaging) is **not** a 1B concern — it applies during BOM explosion to purchase quantities (Phase 1C); no task here needs it.
