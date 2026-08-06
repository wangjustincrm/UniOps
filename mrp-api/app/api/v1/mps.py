"""MPS run API (Phase 1B Task 4, design §6.5).

Wires the pure `app/services/mps_engine.py::generate_mps` to real data:

1. **Demand** — a confirmed forecast version's monthly net requirement per
   material, computed exactly the way `GET /net-requirement` does (this
   module reuses that endpoint's own helpers — `_generate_months` and
   `_load_forecast_by_material` from `app/api/v1/net_requirement.py`, plus
   `compute_net_requirements`/`get_opening_stock_breakdown` from
   `app/services/net_requirement.py` — rather than re-deriving the
   opening-stock math a second time; see that module's docstring for the
   formulas). Only months with `net_requirement > 0` become a `DemandItem`
   — a material whose opening stock already covers every month's forecast
   never appears in the generated MPS at all.

2. **Capacity** — `app/services/capacity.py::resolve_effective_rules`
   resolved at a single reference month, filtered to `scope_type='factory'`
   (Phase 1B only creates factory-wide rules; the schema stays general for a
   later product_family/line scope, so this module explicitly ignores any
   other scope rather than accidentally consuming it).

   **Known simplification, not an oversight**: `generate_mps` takes exactly
   ONE `CapacityLimits` for the whole run, so `POST /runs` and
   `POST .../recalculate` resolve capacity rules effective at the run's own
   `horizon_start_month` and use that single snapshot for every month in the
   horizon. A capacity rule that changes mid-horizon (e.g. a new line
   commissioned in month 6) is NOT picked up mid-run — per-month-varying
   capacity is out of Phase 1B scope (see the design brief). `GET /runs/{id}`
   is more precise where it can afford to be: its capacity_occupancy re-
   resolves rules per plan_month actually present, since that's a read-only
   report, not a scheduling input.

3. **Shelf life** — `app.services.mdm_client.resolve_shelf_life` (added
   alongside this task, mirroring `resolve_material_names`), imported here
   as a bare name — not accessed via the `mdm_client` module — so tests can
   `monkeypatch.setattr(mps, "resolve_shelf_life", ...)` without ever
   reaching real mdm-api, the same idiom `app/api/v1/consignment.py` uses
   for `lookup_lot` (see that module's docstring). A material missing from
   mdm-api's response (or mdm-api being unreachable entirely) resolves to
   `None` — the engine's fail-safe "never pre-build an unknown shelf life"
   behavior, not a hang or a 5xx.

`POST /runs` requires the forecast version to be `status='confirmed'`
(409 otherwise) — this module always plans off "the confirmed forecast",
never a still-editable draft, so a run's demand basis can't shift out from
under a planner mid-review. This mirrors `forecast.py`'s own
draft/immutable status contract, just from the read side.

A run's `status` starts `'draft'` and only ever advances to `'released'` via
`POST .../confirm-release` (this task doesn't use the `'confirmed'` status
value the model reserves — no endpoint here produces it). A released run is
immutable: `POST .../recalculate`, `PATCH .../lines/{id}`, and a second
`POST .../confirm-release` all 409 once `status='released'`.

`confirm-release` deletes EVERY prior `demand_type='mps'` row in
`mrp_demands` — system-wide, regardless of which `forecast_version_id`
produced it — before inserting this run's own rows. This is deliberately
not scoped to "runs of the same forecast_version_id": `ForecastVersion` is
single-lineage (confirming a new version supersedes whichever one was
previously confirmed — see `app/models/forecast.py`), so a re-confirmed
forecast mints a brand-new `version_id` on every planning cycle. Scoping the
delete to `run.forecast_version_id` would leave a prior cycle's released
run's rows behind forever (they belong to the now-superseded version_id),
silently double-counting demand. Only one forecast version — and therefore
only one released MPS lineage — is ever meant to be "live" at a time, so the
delete is unconditional across all `demand_type='mps'` rows; other demand
types (should any exist later) are untouched.

`capacity_gap=True` lines are NEVER written to `mrp_demands`. A gap line is
an *unmet-demand exception* for a human to resolve (add capacity / adjust
the plan), not a booked production order — per `mps_engine.py`'s own
docstring, it "does not consume any month's capacity ledger". Writing it as
ordinary `demand_type='mps'` demand would make Phase 1C's material
explosion over-procure raw materials for production that literally cannot
happen this cycle. The line is still persisted as an `MrpMpsLine` (visible
in the run detail/report) — only the `mrp_demands` materialization skips it.
Once a planner resolves the gap and regenerates, a placed line flows through
normally on the next release.

Permission keys (identity-api/scripts/seed_authz.py, `mrp` app):
`mrp.run.execute` gates generate/recalculate/line-edit; `mrp.report.view`
gates the read; `mrp.proposal.confirm` gates release.
"""
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, func, select

from app.api.v1.net_requirement import _generate_months, _load_forecast_by_material
from app.core.authz import require_permission
from app.core.deps import BearerToken, SessionDep
from app.models.demand import MrpDemand
from app.models.forecast import ForecastVersion
from app.models.mps import MrpMpsLine, MrpMpsRun
from app.services.capacity import resolve_effective_rules
from app.services.mdm_client import resolve_shelf_life
from app.services.mps_engine import CapacityLimits, DemandItem, PlannedLine, generate_mps
from app.services.net_requirement import compute_net_requirements, get_opening_stock_breakdown

router = APIRouter(prefix="/mps", tags=["mps"])

RunDep = Annotated[dict, Depends(require_permission("mrp.run.execute"))]
ReportDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]
ConfirmDep = Annotated[dict, Depends(require_permission("mrp.proposal.confirm"))]

# design §6.8 "保质期的 1/3" — a product may be pre-built up to 1/3 of its
# shelf life early by default when the caller doesn't specify a margin.
DEFAULT_SAFETY_MARGIN_FRACTION = Decimal("0.3333")

# Arbitrary fixed key for the run_no generation advisory lock -- serializes
# concurrent POST /runs calls so two simultaneous requests never compute the
# same "next number" from a stale read and collide on run_no's unique
# constraint (see feedback_uniops_document_number_collision, project
# memory). pg_advisory_xact_lock auto-releases at commit/rollback.
_RUN_NO_LOCK_KEY = 778899221

# Mirrors app/api/v1/forecast.py's _MONTH_RE exactly -- 'YYYY-MM' with a
# real month 01-12. A PATCH .../lines/{id} that accepted a malformed
# plan_month would corrupt GET .../{id}'s capacity_occupancy month grouping
# (a bad key that never matches any resolved capacity month, and sorts
# unpredictably against real 'YYYY-MM' strings).
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# ── Schemas ──────────────────────────────────────────────────────────────


class MpsRunCreate(BaseModel):
    forecast_version_id: uuid.UUID
    safety_margin_fraction: Decimal | None = None


class MpsLineResponse(BaseModel):
    id: uuid.UUID
    material_code: str
    demand_month: str
    plan_month: str
    qty: Decimal
    is_prebuild: bool
    prebuild_reason: str | None
    shelf_life_ok: bool
    capacity_gap: bool
    locked_by_planner: bool
    manual_adjusted: bool
    status: str

    model_config = {"from_attributes": True}


class MpsRunResponse(BaseModel):
    id: uuid.UUID
    run_no: str
    forecast_version_id: uuid.UUID
    horizon_start_month: str
    horizon_months: int
    status: str
    safety_margin_fraction: Decimal
    generated_by: uuid.UUID | None
    stats: dict | None

    model_config = {"from_attributes": True}


class MpsRunDetailResponse(MpsRunResponse):
    lines: list[MpsLineResponse]


class CapacityOccupancyMonth(BaseModel):
    month: str
    used_sku_count: int
    used_qty: Decimal
    max_sku_count: int | None
    max_output_qty: Decimal | None


class MpsRunGetResponse(MpsRunDetailResponse):
    # Computed on read, never stored (Task 4 brief) -- always reflects the
    # rules currently on file, not a stale snapshot from generation time.
    capacity_occupancy: list[CapacityOccupancyMonth]


class MpsLineUpdate(BaseModel):
    qty: Decimal | None = None
    plan_month: str | None = None
    locked_by_planner: bool | None = None

    @field_validator("plan_month")
    @classmethod
    def _valid_month(cls, v: str | None) -> str | None:
        if v is not None and not _MONTH_RE.match(v):
            raise ValueError("plan_month must be 'YYYY-MM'")
        return v


# ── Helpers ──────────────────────────────────────────────────────────────


def _sub_to_uuid(payload: dict) -> uuid.UUID | None:
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return uuid.UUID(sub)
    except ValueError:
        return None


async def _get_version_or_404(db: SessionDep, version_id: uuid.UUID) -> ForecastVersion:
    version = await db.get(ForecastVersion, version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="forecast version not found")
    return version


async def _get_run_or_404(db: SessionDep, run_id: uuid.UUID) -> MrpMpsRun:
    run = await db.get(MrpMpsRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MPS run not found")
    return run


async def _get_line_or_404(db: SessionDep, run_id: uuid.UUID, line_id: uuid.UUID) -> MrpMpsLine:
    line = await db.get(MrpMpsLine, line_id)
    if line is None or line.run_id != run_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MPS line not found")
    return line


def _require_not_released(run: MrpMpsRun) -> None:
    if run.status == "released":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MPS run {run.run_no} is already released and immutable",
        )


async def _load_lines(db: SessionDep, run_id: uuid.UUID) -> list[MrpMpsLine]:
    rows = (await db.execute(
        select(MrpMpsLine)
        .where(MrpMpsLine.run_id == run_id)
        .order_by(MrpMpsLine.plan_month, MrpMpsLine.material_code)
    )).scalars().all()
    return rows


async def _build_demand_items(db: SessionDep, version: ForecastVersion) -> list[DemandItem]:
    """Same net-requirement computation `GET /net-requirement` performs (see
    module docstring) — only positive net requirement becomes demand."""
    months = _generate_months(version.horizon_start_month, version.horizon_months)
    by_material = await _load_forecast_by_material(db, version.id, months)

    demands: list[DemandItem] = []
    for material_code, forecast_cells in by_material.items():
        forecast_by_month = {m: forecast_cells.get(m, Decimal("0")) for m in months}
        breakdown = await get_opening_stock_breakdown(db, material_code)
        for row in compute_net_requirements(forecast_by_month, breakdown.opening_stock):
            if row.net_requirement > 0:
                demands.append(DemandItem(
                    material_code=material_code, demand_month=row.month, qty=row.net_requirement,
                ))
    return demands


async def _resolve_capacity_limits(db: SessionDep, on_month: str) -> CapacityLimits:
    rules = await resolve_effective_rules(db, on_month)
    max_sku_count: int | None = None
    max_output_qty: Decimal | None = None
    for rule in rules:
        if rule.scope_type != "factory":
            continue  # Phase 1B only wires factory-wide rules into the engine (see module docstring)
        if rule.constraint_type == "max_sku_count":
            max_sku_count = int(rule.limit_value)
        elif rule.constraint_type == "max_output_qty":
            max_output_qty = Decimal(rule.limit_value)
    return CapacityLimits(max_sku_count=max_sku_count, max_output_qty=max_output_qty)


async def _next_run_no(db: SessionDep) -> str:
    """`MPS-YYYYMMDD-####`, max-tail+1 among today's existing run numbers,
    serialized by an advisory lock so two concurrent POST /runs never race
    to the same number (see this module's `_RUN_NO_LOCK_KEY` comment)."""
    await db.execute(select(func.pg_advisory_xact_lock(_RUN_NO_LOCK_KEY)))
    today = datetime.now(timezone.utc).date()
    prefix = f"MPS-{today:%Y%m%d}-"
    existing = (await db.execute(
        select(MrpMpsRun.run_no).where(MrpMpsRun.run_no.like(f"{prefix}%"))
    )).scalars().all()
    max_tail = 0
    for run_no in existing:
        try:
            max_tail = max(max_tail, int(run_no[len(prefix):]))
        except ValueError:
            continue  # not one of ours (shouldn't happen given the LIKE filter) -- ignore
    return f"{prefix}{max_tail + 1:04d}"


def _compute_stats(lines: list[PlannedLine]) -> dict:
    return {
        "line_count": len(lines),
        "prebuild_count": sum(1 for l in lines if l.is_prebuild),
        "capacity_gap_count": sum(1 for l in lines if l.capacity_gap),
    }


async def _run_detail_response(db: SessionDep, run: MrpMpsRun) -> MpsRunDetailResponse:
    lines = await _load_lines(db, run.id)
    return MpsRunDetailResponse(
        id=run.id, run_no=run.run_no, forecast_version_id=run.forecast_version_id,
        horizon_start_month=run.horizon_start_month, horizon_months=run.horizon_months,
        status=run.status, safety_margin_fraction=run.safety_margin_fraction,
        generated_by=run.generated_by, stats=run.stats,
        lines=[MpsLineResponse.model_validate(l) for l in lines],
    )


async def _compute_capacity_occupancy(
    db: SessionDep, lines: list[MrpMpsLine],
) -> list[CapacityOccupancyMonth]:
    """Used qty/SKU count per plan_month vs. the rules effective for that
    month, re-resolved fresh on every read (never stored — Task 4 brief).
    `capacity_gap` lines are excluded: they represent an un-placed shortfall,
    not a booked production slot, and don't consume any month's ledger (see
    mps_engine.py's docstring)."""
    by_month: dict[str, tuple[set[str], Decimal]] = {}
    for line in lines:
        if line.capacity_gap:
            continue
        skus, qty = by_month.get(line.plan_month, (set(), Decimal("0")))
        skus.add(line.material_code)
        by_month[line.plan_month] = (skus, qty + line.qty)

    occupancy: list[CapacityOccupancyMonth] = []
    for month in sorted(by_month):
        skus, qty = by_month[month]
        limits = await _resolve_capacity_limits(db, month)
        occupancy.append(CapacityOccupancyMonth(
            month=month, used_sku_count=len(skus), used_qty=qty,
            max_sku_count=limits.max_sku_count, max_output_qty=limits.max_output_qty,
        ))
    return occupancy


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post("/runs", response_model=MpsRunDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_run(body: MpsRunCreate, db: SessionDep, payload: RunDep, token: BearerToken):
    version = await _get_version_or_404(db, body.forecast_version_id)
    if version.status != "confirmed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"forecast version {version.id} is '{version.status}', not 'confirmed' — "
                   "MPS can only be generated from a confirmed forecast",
        )

    safety_margin = body.safety_margin_fraction
    if safety_margin is None:
        safety_margin = DEFAULT_SAFETY_MARGIN_FRACTION

    demands = await _build_demand_items(db, version)
    limits = await _resolve_capacity_limits(db, version.horizon_start_month)
    shelf_life = await resolve_shelf_life(token)
    lines = generate_mps(demands, limits, shelf_life, safety_margin)

    run_no = await _next_run_no(db)
    run = MrpMpsRun(
        run_no=run_no,
        forecast_version_id=version.id,
        horizon_start_month=version.horizon_start_month,
        horizon_months=version.horizon_months,
        status="draft",
        safety_margin_fraction=safety_margin,
        generated_by=_sub_to_uuid(payload),
        stats=_compute_stats(lines),
    )
    db.add(run)
    await db.flush()  # assign run.id for the lines' FK below

    for line in lines:
        db.add(MrpMpsLine(
            run_id=run.id, material_code=line.material_code, demand_month=line.demand_month,
            plan_month=line.plan_month, qty=line.qty, is_prebuild=line.is_prebuild,
            prebuild_reason=line.prebuild_reason, shelf_life_ok=line.shelf_life_ok,
            capacity_gap=line.capacity_gap, locked_by_planner=False, manual_adjusted=False,
        ))

    await db.commit()
    await db.refresh(run)
    return await _run_detail_response(db, run)


@router.get("/runs/{run_id}", response_model=MpsRunGetResponse)
async def get_run(run_id: uuid.UUID, db: SessionDep, _: ReportDep):
    run = await _get_run_or_404(db, run_id)
    lines = await _load_lines(db, run.id)
    occupancy = await _compute_capacity_occupancy(db, lines)
    return MpsRunGetResponse(
        id=run.id, run_no=run.run_no, forecast_version_id=run.forecast_version_id,
        horizon_start_month=run.horizon_start_month, horizon_months=run.horizon_months,
        status=run.status, safety_margin_fraction=run.safety_margin_fraction,
        generated_by=run.generated_by, stats=run.stats,
        lines=[MpsLineResponse.model_validate(l) for l in lines],
        capacity_occupancy=occupancy,
    )


@router.post("/runs/{run_id}/recalculate", response_model=MpsRunDetailResponse)
async def recalculate_run(run_id: uuid.UUID, db: SessionDep, payload: RunDep, token: BearerToken):
    run = await _get_run_or_404(db, run_id)
    _require_not_released(run)
    version = await _get_version_or_404(db, run.forecast_version_id)

    existing_lines = await _load_lines(db, run.id)
    locked_existing = [l for l in existing_lines if l.locked_by_planner]
    locked_planned = [
        PlannedLine(
            material_code=l.material_code, demand_month=l.demand_month, plan_month=l.plan_month,
            qty=l.qty, is_prebuild=l.is_prebuild, prebuild_reason=l.prebuild_reason,
            shelf_life_ok=l.shelf_life_ok, capacity_gap=l.capacity_gap, locked=True,
        )
        for l in locked_existing
    ]
    # A locked line's own manual_adjusted flag must survive being echoed
    # back through generate_mps (PlannedLine has no manual_adjusted field --
    # it's a planner-facing DB concept, not something the pure engine knows
    # about), so remember it here keyed by the identity generate_mps uses to
    # recognize "this is the same locked line" (material + demand_month).
    locked_manual_adjusted = {(l.material_code, l.demand_month): l.manual_adjusted for l in locked_existing}
    locked_keys = set(locked_manual_adjusted)

    # Demand for a material/month already covered by a locked line must NOT
    # be re-submitted to generate_mps -- the locked line already represents
    # that demand and already occupies its month's capacity ledger (seeded
    # from `locked`); re-adding it to `demands` would double-place it.
    demands = [
        d for d in await _build_demand_items(db, version)
        if (d.material_code, d.demand_month) not in locked_keys
    ]
    limits = await _resolve_capacity_limits(db, run.horizon_start_month)
    shelf_life = await resolve_shelf_life(token)
    lines = generate_mps(demands, limits, shelf_life, run.safety_margin_fraction, locked=locked_planned)

    await db.execute(delete(MrpMpsLine).where(MrpMpsLine.run_id == run.id))
    for line in lines:
        manual_adjusted = locked_manual_adjusted.get((line.material_code, line.demand_month), False) if line.locked else False
        db.add(MrpMpsLine(
            run_id=run.id, material_code=line.material_code, demand_month=line.demand_month,
            plan_month=line.plan_month, qty=line.qty, is_prebuild=line.is_prebuild,
            prebuild_reason=line.prebuild_reason, shelf_life_ok=line.shelf_life_ok,
            capacity_gap=line.capacity_gap, locked_by_planner=line.locked, manual_adjusted=manual_adjusted,
        ))
    run.stats = _compute_stats(lines)

    await db.commit()
    await db.refresh(run)
    return await _run_detail_response(db, run)


@router.patch("/runs/{run_id}/lines/{line_id}", response_model=MpsLineResponse)
async def update_line(run_id: uuid.UUID, line_id: uuid.UUID, body: MpsLineUpdate, db: SessionDep, _: RunDep):
    run = await _get_run_or_404(db, run_id)
    _require_not_released(run)
    line = await _get_line_or_404(db, run_id, line_id)

    changed = False
    if body.qty is not None and body.qty != line.qty:
        line.qty = body.qty
        changed = True
    if body.plan_month is not None and body.plan_month != line.plan_month:
        line.plan_month = body.plan_month
        changed = True
    if body.locked_by_planner is not None:
        line.locked_by_planner = body.locked_by_planner
    if changed:
        line.manual_adjusted = True

    await db.commit()
    await db.refresh(line)
    return line


@router.post("/runs/{run_id}/confirm-release", response_model=MpsRunDetailResponse)
async def confirm_release(run_id: uuid.UUID, db: SessionDep, _: ConfirmDep):
    run = await _get_run_or_404(db, run_id)
    _require_not_released(run)
    lines = await _load_lines(db, run.id)

    # Delete EVERY prior demand_type='mps' row, system-wide -- not scoped to
    # this run's forecast_version_id. See module docstring for why: a
    # re-confirmed forecast mints a new version_id each cycle, so scoping
    # this delete to `run.forecast_version_id` would leave a superseded
    # cycle's released rows behind forever (they belong to a different
    # version_id) and silently double-count demand. Only one forecast
    # version — and therefore only one released MPS lineage — is ever live.
    await db.execute(delete(MrpDemand).where(MrpDemand.demand_type == "mps"))

    for line in lines:
        if line.capacity_gap:
            # Unmet-demand exception, not a booked production order -- never
            # materialized into mrp_demands (see module docstring). Still
            # persisted as an MrpMpsLine above, so it stays visible.
            continue
        db.add(MrpDemand(
            source_run_id=run.id, demand_type="mps", material_code=line.material_code,
            demand_month=line.plan_month, qty=line.qty,
        ))

    run.status = "released"
    await db.commit()
    await db.refresh(run)
    return await _run_detail_response(db, run)
