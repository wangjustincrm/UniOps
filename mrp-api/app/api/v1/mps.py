"""MPS run API (design §2.0/§6.5; weekly since the 2026-08-12 rework).

Wires the pure `app/services/mps_engine.py::generate_mps` to real data:

1. **Demand** — a confirmed forecast version's monthly net requirement per
   material, computed exactly the way `GET /net-requirement` does (this
   module reuses that endpoint's own helpers — `_generate_months` and
   `_load_forecast_by_material` from `app/api/v1/net_requirement.py`, plus
   `compute_net_requirements`/`get_opening_stock_breakdown` from
   `app/services/net_requirement.py` — rather than re-deriving the
   opening-stock math a second time; see that module's docstring for the
   formulas). Demand stays MONTHLY (design D1: the sales forecast is not
   weekly) — the engine is what turns a demand month into weeks. Only
   months with `net_requirement > 0` become a `DemandItem` — a material
   whose opening stock already covers every month's forecast never appears
   in the generated MPS at all.

2. **Capacity** — `app/services/capacity.py::resolve_limits_for_week`,
   resolved **per week**, including that week's `MrpCapacityException`
   overrides (a maintenance week is `max_output_qty=0`). The engine takes a
   `limits_for_week(week) -> CapacityLimits` callable, but it is a pure
   synchronous function and the resolver is async, so `_week_limits_lookup`
   pre-resolves every week the run can possibly touch (see
   `_planning_weeks`) into a dict and hands the engine a lookup over it. A
   week the engine asks for that is NOT in that dict raises loudly rather
   than degrading to "unlimited": a silent unlimited week would produce a
   plan that ignores a shutdown.

   This replaced a month-based `resolve_effective_rules` resolved ONCE at
   `horizon_start_month` and applied to the whole horizon. Both had to move
   in the same change — a monthly ceiling still feeding a weekly engine is
   roughly 4x the real weekly capacity and looks perfectly replaced from the
   outside (migration `mrp10b` deactivates the standing rules for the same
   reason).

3. **Shelf life** — `app.services.mdm_client.resolve_shelf_life`, imported
   here as a bare name — not accessed via the `mdm_client` module — so tests
   can `monkeypatch.setattr(mps, "resolve_shelf_life", ...)` without ever
   reaching real mdm-api, the same idiom `app/api/v1/consignment.py` uses
   for `lookup_lot` (see that module's docstring). A material missing from
   mdm-api's response (or mdm-api being unreachable entirely) resolves to
   `None` — the engine's fail-safe "never pre-build an unknown shelf life"
   behavior, not a hang or a 5xx.

   Because that fail-safe silently degrades pre-build to "never move", and
   because ERP's `exp` field has never been verified to be populated for
   finished goods (design §9), **the generate/recalculate summary names
   every product it planned with no shelf life on record**:
   `stats["no_shelf_life"] = [{"code", "name"}]` (design §7). A plan that
   cannot pre-build anything now says so instead of just looking
   capacity-tight.

## A run snapshots the calendar it was generated under

`week_calendar_mode` and `production_lead_weeks` are stored ON THE RUN at
generate time. `GET /runs/{id}`, `POST .../recalculate` and
`PATCH .../lines/{id}` all read them back off the run and NEVER off the
current `mrp_planning_params` row or the request body. Changing the
factory's week definition must not reshape a plan somebody has already
reviewed or released — design §5.4 states this to the planner in the
settings UI, and this module is where it is actually true.

`POST /runs` requires the forecast version to be `status='confirmed'`
(409 otherwise) — this module always plans off "the confirmed forecast",
never a still-editable draft, so a run's demand basis can't shift out from
under a planner mid-review. This mirrors `forecast.py`'s own
draft/immutable status contract, just from the read side.

A run's `status` starts `'draft'` and only ever advances to `'released'` via
`POST .../confirm-release` (this module doesn't use the `'confirmed'` status
value the model reserves — no endpoint here produces it). A released run is
immutable: `POST .../recalculate`, `PATCH .../lines/{id}`, and a second
`POST .../confirm-release` all 409 once `status='released'`.

## Locked lines

A planner locks a line to say "this production is committed, do not move
it". `recalculate_run` rebuilds each locked row into a
`mps_engine.WeeklyLine(locked=True)` and passes it to `generate_mps`, which
seeds it into its week's load (via `pack_bucket`'s `preloaded`) and
subtracts its quantity from the matching `(material_code, demand_month)`
demand BY WEEK, leaving the open remainder to be re-planned. The engine
echoes the locked line back byte for byte. This module therefore must NOT
drop the whole `(material_code, demand_month)` demand key the way the
month-based version did — that deleted the un-locked remainder.

Two consequences this module is responsible for:

- **A `capacity_gap` line cannot be locked** (422 from
  `PATCH .../lines/{id}`). The engine deliberately DROPS locked gap lines: a
  gap is derived data that this very run recomputes, and echoing a stale one
  double-counts the demand. If the API let a planner lock one anyway, the
  next recalculate would silently clear that row's `locked_by_planner`,
  `manual_adjusted` and demand-context snapshot. Refusing the lock up front
  is the only version of this a planner can see. (The alternative
  considered — re-applying the lock by `(material_code, demand_month)` —
  is not well defined weekly: one demand month spans several lines, so it
  would re-lock an arbitrary different row.)
- Fields the pure engine has no concept of (`manual_adjusted`, the
  `demand_forecast`/`opening_stock` snapshot) are carried across the
  recalculate by `(material_code, demand_month, plan_week_start)` — the
  full slot identity, because weekly a `(material, demand_month)` pair
  spans several lines and keying on the pair alone would copy one line's
  flags onto another's.

`confirm-release` deletes EVERY prior `demand_type='mps'` row in
`mrp_demands` — system-wide, regardless of which `forecast_version_id`
produced it — before inserting this run's own rows. This is deliberately
not scoped to "runs of the same forecast_version_id": multiple
`ForecastVersion`s can be `confirmed` at once (outlook snapshots coexist —
see `app/models/forecast.py`), but that is a forecasting-side fact only.
Regardless of how many forecast versions are confirmed, only one MPS
lineage is meant to be live/released at a time (design §8, "one active
released plan") — releasing a new run always replaces whichever plan was
previously active, even one built off a different confirmed outlook.
Scoping the delete to `run.forecast_version_id` would leave a prior
release's rows behind forever whenever it was built off a different
version_id, silently double-counting demand. So the delete is unconditional
across all `demand_type='mps'` rows; other demand types (should any exist
later) are untouched. (The UI is responsible for warning a planner before
releasing a run that was built off a non-latest confirmed outlook — this
endpoint itself does not block or check outlook recency.) Every row written
carries `plan_week_start` alongside `demand_month` (= the line's
`plan_week_month`) for Phase 1C's material explosion.

`capacity_gap=True` lines are NEVER written to `mrp_demands`. A gap line is
an *unmet-demand exception* for a human to resolve (add capacity / adjust
the plan), not a booked production order — per `mps_engine.py`'s own
docstring, it "does not consume any week's capacity ledger". Writing it as
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
import uuid
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.api.v1.net_requirement import _generate_months, _load_forecast_by_material
from app.api.v1.params import get_param
from app.core.authz import require_permission
from app.core.deps import BearerToken, SessionDep
from app.models.demand import MrpDemand
from app.models.forecast import ForecastLine, ForecastVersion
from app.models.mps import MrpMpsLine, MrpMpsRun
from app.services import mps_export
from app.services.capacity import resolve_limits_for_week
from app.services.mdm_client import resolve_material_names, resolve_shelf_life
from app.services.mps_engine import (
    CapacityLimits, DemandItem, WeeklyLine, _placement_allowed, generate_mps,
)
from app.services.net_requirement import compute_net_requirements, get_opening_stock_breakdown
from app.services.week_calendar import (
    WEEK_MODES, owning_month, shift_weeks, week_label, week_start_of, weeks_of_month,
)

router = APIRouter(prefix="/mps", tags=["mps"])

RunDep = Annotated[dict, Depends(require_permission("mrp.run.execute"))]
ReportDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]
ConfirmDep = Annotated[dict, Depends(require_permission("mrp.proposal.confirm"))]

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# design §6.8 "保质期的 1/3" — a product may be pre-built up to 1/3 of its
# shelf life early by default when the caller doesn't specify a margin.
DEFAULT_SAFETY_MARGIN_FRACTION = Decimal("0.3333")

# design §2.6 — four weeks replaces the month-based engine's one-month
# default, i.e. roughly the same distance ahead, expressed in weeks. Mirrors
# MrpMpsRun.production_lead_weeks' own server_default.
DEFAULT_PRODUCTION_LEAD_WEEKS = 4

# app/api/v1/params.py's only whitelisted key today. WEEK_MODES[0]
# ('iso_thursday') is the fallback when the row has never been written --
# the same default `MrpMpsRun.week_calendar_mode` and mrp10a carry.
_WEEK_CALENDAR_MODE_KEY = "week_calendar_mode"

# Arbitrary fixed key for the run_no generation advisory lock -- serializes
# concurrent POST /runs calls so two simultaneous requests never compute the
# same "next number" from a stale read and collide on run_no's unique
# constraint (see feedback_uniops_document_number_collision, project
# memory). pg_advisory_xact_lock auto-releases at commit/rollback.
_RUN_NO_LOCK_KEY = 778899221


# ── Schemas ──────────────────────────────────────────────────────────────


class MpsRunCreate(BaseModel):
    forecast_version_id: uuid.UUID
    safety_margin_fraction: Decimal | None = None
    # WEEKS production is scheduled ahead of a demand month -- fed straight
    # into mps_engine.generate_mps's lead_weeks param. None (omitted) means
    # "use the default" (DEFAULT_PRODUCTION_LEAD_WEEKS), matching
    # safety_margin_fraction's own contract. Bounded 0-52 server-side
    # (design §2.6): the UI already clamps to this range, but a direct API
    # caller must not be able to pass -1 (which would schedule production
    # AFTER its demand month) or a lead longer than the horizon.
    #
    # There is deliberately NO `week_calendar_mode` field: the mode is a
    # factory-wide planning parameter (PUT /params/week_calendar_mode), not
    # a per-run choice. The run records whichever value was in force.
    production_lead_weeks: int | None = Field(default=None, ge=0, le=52)


class MpsLineResponse(BaseModel):
    id: uuid.UUID
    material_code: str
    demand_month: str
    # The week this line is scheduled in, its owning month under the RUN's
    # stored week_calendar_mode (denormalized on the row, never recomputed
    # on read), and a rendered label for that mode ('2026-W32 · Aug 3–9' /
    # 'Aug W2 · Aug 8–14'). `week_label` is the only one computed on read:
    # it is pure presentation derived from the two stored values, so there
    # is nothing for it to drift against.
    plan_week_start: date
    plan_week_month: str
    week_label: str
    # How many whole weeks earlier than its lead-shifted target week this
    # line landed. **This -- not `is_prebuild` -- is the field that answers
    # "is this line early".** `is_prebuild` asks a strictly narrower
    # question (did the production cross into an EARLIER month than the
    # demand's own bucket), so ordinary levelling inside the demand's own
    # month reads `weeks_early > 0, is_prebuild=False`. See
    # mps_engine.WeeklyLine's field comments.
    weeks_early: int
    qty: Decimal
    is_prebuild: bool
    prebuild_reason: str | None
    shelf_life_ok: bool
    capacity_gap: bool
    locked_by_planner: bool
    manual_adjusted: bool
    status: str
    # Demand context (design: Production Plan matrix) -- snapshotted onto
    # MrpMpsLine at generate/recalculate time (mrp07 migration) so a
    # released run's numbers never drift as live inventory moves afterward,
    # and GET .../{id} doesn't pay a full net-requirement rollforward on
    # every read. `_line_response` reads these straight off the line,
    # defaulting NULL (pre-mrp07 lines) to 0 -- never a live recompute.
    demand_forecast: Decimal = Decimal("0")
    opening_stock: Decimal = Decimal("0")
    # True when the run's production_lead_weeks called for production to
    # have already started (the target week was clamped to the current
    # week) -- see mps_engine.generate_mps's pipeline docstring, step 2.
    lead_shortfall: bool = False


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
    # Snapshotted at generate time and never re-read from the current
    # planning parameters -- see this module's docstring, "A run snapshots
    # the calendar it was generated under".
    production_lead_weeks: int
    week_calendar_mode: str


class MpsRunDetailResponse(MpsRunResponse):
    lines: list[MpsLineResponse]


class CapacityOccupancyWeek(BaseModel):
    week_start: date
    week_month: str
    week_label: str
    used_sku_count: int
    used_qty: Decimal
    max_sku_count: int | None
    max_output_qty: Decimal | None


class MpsRunGetResponse(MpsRunDetailResponse):
    # Computed on read, never stored -- always reflects the capacity rules
    # and week exceptions currently on file, not a snapshot from generation
    # time. (The run's WEEK GRID is snapshotted -- see the module docstring
    # -- so a released plan keeps its columns; what those columns are
    # measured against is deliberately live, because "am I over the ceiling
    # I have today" is the question a planner is asking when they look.)
    capacity_occupancy: list[CapacityOccupancyWeek]


class MpsLineUpdate(BaseModel):
    qty: Decimal | None = None
    # A week START date on the RUN's own grid -- `update_line` 422s anything
    # else (a Wednesday under an ISO mode, the 9th under month_fixed). An
    # off-grid date would put the line in a "week" no capacity resolver,
    # occupancy rollup or export column can ever match.
    plan_week_start: date | None = None
    locked_by_planner: bool | None = None


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
        .order_by(MrpMpsLine.plan_week_start, MrpMpsLine.material_code)
    )).scalars().all()
    return rows


async def _load_intent_lines(db: SessionDep, version: ForecastVersion) -> list[ForecastLine]:
    """This version's frozen intent-product rows (Task 4) — filtered purely
    on the frozen `is_intent` column, NEVER by re-deriving from the material
    code via `is_intent_code()`. That's the whole reason `freeze_outlook`
    (`app/services/demand_series.py`) writes `is_intent`/`intent_name` onto
    each `ForecastLine` at freeze time instead of joining `mrp_intent_products`
    here: a snapshot must stay self-explanatory even after its intent code is
    later bound to a real material or dropped, and those are exactly the rows
    a code-prefix guess would get wrong. Restricted to the version's current
    horizon the same way `_load_forecast_by_material` is (a line surviving
    outside the horizon after an edit shouldn't surface here either)."""
    months = set(_generate_months(version.horizon_start_month, version.horizon_months))
    rows = (await db.execute(
        select(ForecastLine).where(
            ForecastLine.version_id == version.id, ForecastLine.is_intent.is_(True),
        )
    )).scalars().all()
    return [line for line in rows if line.month in months]


def _skipped_intent_stats(intent_lines: list[ForecastLine]) -> list[dict]:
    return [
        {"code": l.material_code, "name": l.intent_name, "qty": str(l.qty)}
        for l in intent_lines
    ]


async def _build_demand_items(
    db: SessionDep, version: ForecastVersion, exclude_codes: frozenset[str] = frozenset(),
) -> list[DemandItem]:
    """Same net-requirement computation `GET /net-requirement` performs (see
    module docstring) — only positive net requirement becomes demand.
    `exclude_codes` (the version's intent-product material codes, see
    `_load_intent_lines`) are skipped entirely — an intent product has no
    real ERP material, so it must never reach the planning engine or a
    persisted `MrpMpsLine`."""
    months = _generate_months(version.horizon_start_month, version.horizon_months)
    by_material = await _load_forecast_by_material(db, version.id, months)

    demands: list[DemandItem] = []
    for material_code, forecast_cells in by_material.items():
        if material_code in exclude_codes:
            continue
        forecast_by_month = {m: forecast_cells.get(m, Decimal("0")) for m in months}
        breakdown = await get_opening_stock_breakdown(db, material_code)
        for row in compute_net_requirements(forecast_by_month, breakdown.opening_stock):
            if row.net_requirement > 0:
                demands.append(DemandItem(
                    material_code=material_code, demand_month=row.month, qty=row.net_requirement,
                ))
    return demands


async def _resolve_week_mode(db: SessionDep) -> str:
    """The factory-wide week definition currently in force, for a run being
    generated NOW. Every other endpoint reads the mode off the RUN instead
    (module docstring). An unrecognised stored value is refused rather than
    defaulted: `week_calendar.py` refuses to guess for exactly the same
    reason, and a typo'd mode silently reverting to `iso_thursday` would
    re-bucket the whole factory's plan with no signal."""
    mode = await get_param(db, _WEEK_CALENDAR_MODE_KEY, WEEK_MODES[0])
    if mode not in WEEK_MODES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"planning parameter {_WEEK_CALENDAR_MODE_KEY} is {mode!r}, "
                   f"which is not one of {WEEK_MODES}; fix it before generating a plan",
        )
    return mode


def _month_span(first: str, last: str) -> list[str]:
    """Every 'YYYY-MM' from `first` to `last` inclusive (empty if reversed)."""
    start = int(first[:4]) * 12 + int(first[5:7]) - 1
    end = int(last[:4]) * 12 + int(last[5:7]) - 1
    return [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(start, end + 1)]


def _planning_weeks(current: date, demand_months: list[str], mode: str) -> list[date]:
    """Every week `generate_mps` can possibly ask `limits_for_week` about.

    The engine only ever touches weeks `>= current_week` (a bucket canvas is
    filtered on it, and the backward walk breaks the moment it steps before
    it), and never later than the last week of the LATEST bucket month. A
    bucket month is the owning month of a lead-shifted target week, and a
    target is never later than the last week of its own demand month --
    whose owning month is that demand month by construction. So the upper
    bound is the last demand month, and the lower bound is the current
    week's own owning month (the clamp puts every overdue demand there, and
    the backward walk can travel down to it from a much later bucket).

    Returned in ascending order, deduplicated. Under the ISO modes a week
    owned by month M can START in M-1, which is exactly why this iterates
    `weeks_of_month` per month rather than stepping days.
    """
    here = owning_month(current, mode)
    first = min([here] + demand_months)
    last = max([here] + demand_months)
    weeks: set[date] = set()
    for month in _month_span(first, last):
        weeks.update(w for w in weeks_of_month(month, mode) if w >= current)
    return sorted(weeks)


async def _week_limits_lookup(
    db: SessionDep, weeks: list[date],
) -> Callable[[date], CapacityLimits]:
    """Pre-resolve `weeks` and hand back the pure lookup `generate_mps`
    wants. The engine is synchronous by design (no DB, no clock), so the
    async resolver cannot be called from inside it.

    A miss raises rather than returning unlimited capacity. `_planning_weeks`
    is supposed to be a superset of what the engine asks for; if that ever
    stops being true, the failure must be a loud 500 naming the week, not a
    plan that quietly ignores a shutdown week because nobody resolved it.
    """
    table = {week: await resolve_limits_for_week(db, week) for week in weeks}

    def _for_week(week: date) -> CapacityLimits:
        try:
            return table[week]
        except KeyError:  # pragma: no cover -- guard, see docstring
            raise RuntimeError(
                f"no capacity resolved for the week of {week.isoformat()}; "
                "_planning_weeks did not cover what the engine asked for"
            ) from None

    return _for_week


def _target_week(demand_month: str, lead_weeks: int, current: date, mode: str) -> date:
    """`generate_mps`'s step 2, for one demand month: the last week of the
    demand month shifted back `lead_weeks`, never before `current`. Kept in
    step with the engine deliberately -- `update_line` needs the same target
    to recompute `weeks_early` for a hand-moved line."""
    standard = shift_weeks(weeks_of_month(demand_month, mode)[-1], -lead_weeks, mode)
    return current if standard < current else standard


def _weeks_between(earlier: date, later: date, mode: str) -> int:
    """Whole week steps from `earlier` up to `later` on this mode's grid; 0
    if `earlier` is not before `later`. Walks with `shift_weeks` rather than
    dividing a day difference by 7 -- under `month_fixed` a week is 1-7 days
    long, so day arithmetic gives the wrong count."""
    if earlier >= later:
        return 0
    steps = 0
    week = earlier
    while week < later:
        week = shift_weeks(week, 1, mode)
        steps += 1
        if steps > 5000:  # pragma: no cover -- off-grid input would never terminate
            raise RuntimeError(
                f"{earlier.isoformat()} is not on the {mode} week grid leading to "
                f"{later.isoformat()}")
    return steps


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


def _compute_stats(lines: list[WeeklyLine]) -> dict:
    return {
        "line_count": len(lines),
        # `is_prebuild` = the line crossed into an earlier MONTH than the
        # demand's own bucket. Levelling inside the bucket is not counted
        # here (it would flag most of a healthy plan -- measured at 87% of
        # the lines of a gap-free plan; see mps_engine.WeeklyLine). Anything
        # asking "how early is this line" must read `weeks_early` instead.
        "prebuild_count": sum(1 for l in lines if l.is_prebuild),
        "capacity_gap_count": sum(1 for l in lines if l.capacity_gap),
    }


async def _no_shelf_life_stats(
    lines: list[WeeklyLine], shelf_life: dict, token: str,
) -> list[dict]:
    """design §7: name the products this run planned with no shelf life on
    record. Unknown shelf life is not an error -- the engine still plans the
    product, it just refuses to pre-build it a single week (fail safe). That
    degradation is invisible from the outside: the plan simply looks
    capacity-tight. ERP's `exp` field has never been verified to be
    populated for finished goods (design §9 residual risk), so this makes
    the whole class of it visible on every run instead.

    Names come from mdm-api on the same degrade-to-`None` contract every
    other read here uses, and are only fetched when there IS something to
    name -- a fully-populated shelf-life map costs no extra call."""
    codes = sorted({l.material_code for l in lines
                    if shelf_life.get(l.material_code) is None})
    if not codes:
        return []
    names = await resolve_material_names(token)
    return [{"code": code, "name": names.get(code)} for code in codes]


async def _run_detail_response(db: SessionDep, run: MrpMpsRun) -> MpsRunDetailResponse:
    lines = await _load_lines(db, run.id)
    return MpsRunDetailResponse(
        id=run.id, run_no=run.run_no, forecast_version_id=run.forecast_version_id,
        horizon_start_month=run.horizon_start_month, horizon_months=run.horizon_months,
        status=run.status, safety_margin_fraction=run.safety_margin_fraction,
        generated_by=run.generated_by, stats=run.stats,
        production_lead_weeks=run.production_lead_weeks,
        week_calendar_mode=run.week_calendar_mode,
        lines=[_line_response(l, run.week_calendar_mode) for l in lines],
    )


async def _build_demand_context(db: SessionDep, version: ForecastVersion) -> dict[str, dict[str, tuple[Decimal, Decimal]]]:
    """Re-derive the run's demand basis exactly as `_build_demand_items` did,
    but keep every month's gross forecast + rolled-forward opening stock
    (not just the positive-net-requirement ones), so `create_run` /
    `recalculate_run` can snapshot them onto each persisted line by
    (material_code, demand_month) at WRITE time (mrp07 migration --
    `MrpMpsLine.demand_forecast`/`opening_stock`). Reads (`get_run`, export)
    no longer call this; they read the stored columns straight off the line
    (see `_line_response`) so a released run's numbers stay frozen instead of
    drifting with live inventory. material -> {month: (forecast_qty,
    opening_stock)}."""
    months = _generate_months(version.horizon_start_month, version.horizon_months)
    by_material = await _load_forecast_by_material(db, version.id, months)

    ctx: dict[str, dict[str, tuple[Decimal, Decimal]]] = {}
    for material_code, forecast_cells in by_material.items():
        forecast_by_month = {m: forecast_cells.get(m, Decimal("0")) for m in months}
        breakdown = await get_opening_stock_breakdown(db, material_code)
        ctx[material_code] = {
            row.month: (row.forecast_qty, row.opening_stock)
            for row in compute_net_requirements(forecast_by_month, breakdown.opening_stock)
        }
    return ctx


def _line_response(line: MrpMpsLine, mode: str) -> MpsLineResponse:
    """Reads the frozen demand-context snapshot straight off the line
    (`MrpMpsLine.demand_forecast`/`opening_stock`, written once at
    generate/recalculate time -- see `_build_demand_context`'s docstring).
    NULL (pre-mrp07 lines, never regenerated) defaults to 0 rather than a
    live recompute.

    `mode` is always the RUN's stored `week_calendar_mode`, never the
    current planning parameter -- it only labels `plan_week_start`, and a
    released plan whose column headings shifted under it because somebody
    edited a setting would be worse than no labels at all."""
    return MpsLineResponse(
        id=line.id, material_code=line.material_code, demand_month=line.demand_month,
        plan_week_start=line.plan_week_start, plan_week_month=line.plan_week_month,
        week_label=week_label(line.plan_week_start, mode),
        weeks_early=line.weeks_early,
        qty=line.qty, is_prebuild=line.is_prebuild,
        prebuild_reason=line.prebuild_reason, shelf_life_ok=line.shelf_life_ok,
        capacity_gap=line.capacity_gap, locked_by_planner=line.locked_by_planner,
        manual_adjusted=line.manual_adjusted, status=line.status,
        demand_forecast=line.demand_forecast if line.demand_forecast is not None else Decimal("0"),
        opening_stock=line.opening_stock if line.opening_stock is not None else Decimal("0"),
        lead_shortfall=line.lead_shortfall,
    )


async def _compute_capacity_occupancy(
    db: SessionDep, lines: list[MrpMpsLine], mode: str,
) -> list[CapacityOccupancyWeek]:
    """Used qty/SKU count per PLAN WEEK vs. the limits effective for that
    week (standing rules plus that week's exceptions), re-resolved fresh on
    every read, never stored.

    `capacity_gap` lines are excluded before anything is counted: they
    represent an un-placed shortfall, not a booked production slot, and
    consume no week's ledger (mps_engine.py's docstring). Counting them
    would report a maintenance week as over capacity purely because the
    demand it could not host is pinned there."""
    by_week: dict = {}
    for line in lines:
        if line.capacity_gap:
            continue
        skus, qty = by_week.get(line.plan_week_start, (set(), Decimal("0")))
        skus.add(line.material_code)
        by_week[line.plan_week_start] = (skus, qty + line.qty)

    occupancy: list[CapacityOccupancyWeek] = []
    for week in sorted(by_week):
        skus, qty = by_week[week]
        limits = await resolve_limits_for_week(db, week)
        occupancy.append(CapacityOccupancyWeek(
            week_start=week, week_month=owning_month(week, mode),
            week_label=week_label(week, mode),
            used_sku_count=len(skus), used_qty=qty,
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
    lead = (body.production_lead_weeks if body.production_lead_weeks is not None
            else DEFAULT_PRODUCTION_LEAD_WEEKS)
    # The ONE place the current planning parameter is read. Everything
    # afterwards -- this request included -- goes through `run.week_calendar_mode`.
    mode = await _resolve_week_mode(db)
    current_week = week_start_of(datetime.now(timezone.utc).date(), mode)

    intent_lines = await _load_intent_lines(db, version)
    intent_codes = frozenset(l.material_code for l in intent_lines)

    demands = await _build_demand_items(db, version, intent_codes)
    limits_for_week = await _week_limits_lookup(db, _planning_weeks(
        current_week, _generate_months(version.horizon_start_month, version.horizon_months), mode,
    ))
    shelf_life = await resolve_shelf_life(token)
    lines = generate_mps(
        demands, limits_for_week, shelf_life, safety_margin,
        lead_weeks=lead, current_week=current_week, mode=mode,
    )
    # Snapshot the demand context (gross forecast + rolled-forward opening
    # stock) onto each line NOW, at generate time -- see
    # `_build_demand_context`'s docstring for why reads no longer recompute
    # this from live inventory.
    ctx = await _build_demand_context(db, version)

    stats = _compute_stats(lines)
    stats["skipped_intent"] = _skipped_intent_stats(intent_lines)
    stats["no_shelf_life"] = await _no_shelf_life_stats(lines, shelf_life, token)

    run_no = await _next_run_no(db)
    run = MrpMpsRun(
        run_no=run_no,
        forecast_version_id=version.id,
        horizon_start_month=version.horizon_start_month,
        horizon_months=version.horizon_months,
        status="draft",
        safety_margin_fraction=safety_margin,
        generated_by=_sub_to_uuid(payload),
        stats=stats,
        production_lead_weeks=lead,
        week_calendar_mode=mode,
    )
    db.add(run)
    await db.flush()  # assign run.id for the lines' FK below

    for line in lines:
        demand_forecast, opening_stock = ctx.get(line.material_code, {}).get(
            line.demand_month, (Decimal("0"), Decimal("0"))
        )
        db.add(MrpMpsLine(
            run_id=run.id, material_code=line.material_code, demand_month=line.demand_month,
            # plan_week_month comes from the engine, which computed it under
            # `mode`; recomputing it here would be a second implementation of
            # week ownership free to disagree with the one that placed the line.
            plan_week_start=line.plan_week_start, plan_week_month=line.plan_week_month,
            weeks_early=line.weeks_early,
            qty=line.qty, is_prebuild=line.is_prebuild,
            prebuild_reason=line.prebuild_reason, shelf_life_ok=line.shelf_life_ok,
            capacity_gap=line.capacity_gap, locked_by_planner=False, manual_adjusted=False,
            demand_forecast=demand_forecast, opening_stock=opening_stock,
            lead_shortfall=line.lead_shortfall,
        ))

    await db.commit()
    await db.refresh(run)
    return await _run_detail_response(db, run)


@router.get("/runs/{run_id}", response_model=MpsRunGetResponse)
async def get_run(run_id: uuid.UUID, db: SessionDep, _: ReportDep):
    run = await _get_run_or_404(db, run_id)
    lines = await _load_lines(db, run.id)
    # The run's OWN mode, never the current parameter (module docstring).
    mode = run.week_calendar_mode
    occupancy = await _compute_capacity_occupancy(db, lines, mode)
    return MpsRunGetResponse(
        id=run.id, run_no=run.run_no, forecast_version_id=run.forecast_version_id,
        horizon_start_month=run.horizon_start_month, horizon_months=run.horizon_months,
        status=run.status, safety_margin_fraction=run.safety_margin_fraction,
        generated_by=run.generated_by, stats=run.stats,
        production_lead_weeks=run.production_lead_weeks,
        week_calendar_mode=mode,
        lines=[_line_response(l, mode) for l in lines],
        capacity_occupancy=occupancy,
    )


@router.get("/runs/{run_id}/export")
async def export_run(
    run_id: uuid.UUID, db: SessionDep, _: ReportDep, token: BearerToken,
    unit: str = Query(default="t", pattern="^(kg|t)$"),
):
    """Production plan matrix (Product x plan_week_month, Demand/Available/Planned
    rows per product) as xlsx — mirrors forecast.py's `GET .../export`
    (openpyxl workbook built off the same per-line demand context `GET
    /runs/{id}` renders, returned as a binary attachment). See
    app/services/mps_export.py's docstring for the aggregation/columns.

    Names resolved the same one-batched-call/degrade-to-code contract every
    other mdm-api-backed read in this service uses (see
    resolve_material_names' docstring) — mdm-api trouble means every product
    row falls back to its bare material_code, never a broken export."""
    run = await _get_run_or_404(db, run_id)
    lines = await _load_lines(db, run.id)
    line_responses = [_line_response(l, run.week_calendar_mode) for l in lines]

    codes = {l.material_code for l in line_responses}
    names = await resolve_material_names(token) if codes else {}

    content = mps_export.build_mps_matrix_workbook(run, line_responses, unit, names)
    filename = f"production-plan-{run.run_no}.xlsx"
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/runs/{run_id}/recalculate", response_model=MpsRunDetailResponse)
async def recalculate_run(run_id: uuid.UUID, db: SessionDep, payload: RunDep, token: BearerToken):
    run = await _get_run_or_404(db, run_id)
    _require_not_released(run)
    version = await _get_version_or_404(db, run.forecast_version_id)

    # The run's OWN calendar and lead, never the current planning parameter
    # or a fresh default -- see this module's docstring.
    mode = run.week_calendar_mode
    lead = run.production_lead_weeks
    current_week = week_start_of(datetime.now(timezone.utc).date(), mode)

    existing_lines = await _load_lines(db, run.id)
    locked_existing = [l for l in existing_lines if l.locked_by_planner]
    # Rebuilt byte for byte, `weeks_early`/`is_prebuild`/`lead_shortfall`
    # included: `generate_mps` echoes a locked line unchanged, so whatever
    # is put in here is exactly what comes back out and gets re-persisted.
    # (`capacity_gap` is never among them -- `update_line` refuses to lock a
    # gap row; see the module docstring. It is still passed through rather
    # than filtered here, so that a gap row locked by some other route would
    # hit the engine's own drop rule instead of being silently laundered.)
    locked_weekly = [
        WeeklyLine(
            material_code=l.material_code, demand_month=l.demand_month,
            plan_week_start=l.plan_week_start, plan_week_month=l.plan_week_month,
            qty=l.qty, is_prebuild=l.is_prebuild, weeks_early=l.weeks_early,
            prebuild_reason=l.prebuild_reason, shelf_life_ok=l.shelf_life_ok,
            capacity_gap=l.capacity_gap, locked=True, lead_shortfall=l.lead_shortfall,
        )
        for l in locked_existing
    ]
    # Two things `WeeklyLine` has no field for and the engine therefore
    # cannot carry: the planner-facing `manual_adjusted` flag, and the
    # already-frozen demand-context snapshot (a point-in-time value from
    # whenever the line was last generated -- NOT something to recompute
    # here, see `_build_demand_context`). Keyed on the FULL slot identity
    # `(material_code, demand_month, plan_week_start)`: weekly, one
    # (material, demand month) pair spans several lines, so the monthly
    # two-part key would copy one line's flags onto a different line.
    locked_manual_adjusted: dict = {}
    locked_demand_context: dict = {}
    for l in locked_existing:
        key = (l.material_code, l.demand_month, l.plan_week_start)
        # OR, not overwrite: two locked rows can share a slot only if a
        # planner moved one onto another, and `_merge_same_slot` will fold
        # them into one output line. A hand-edit on either row must survive.
        locked_manual_adjusted[key] = locked_manual_adjusted.get(key, False) or l.manual_adjusted
        locked_demand_context.setdefault(key, (l.demand_forecast, l.opening_stock))

    intent_lines = await _load_intent_lines(db, version)
    intent_codes = frozenset(l.material_code for l in intent_lines)

    # Same intent exclusion as create_run -- without it, a run generated
    # after intent rows were already skipped would silently re-admit them on
    # the very next recalculate.
    #
    # Demand is NOT filtered by the locked lines' keys. `generate_mps`
    # subtracts each locked quantity from its own (material, demand month)
    # demand BY WEEK and re-plans the remainder; dropping the whole key here
    # -- what the month-based version did -- would delete the part of that
    # month's demand the planner did not lock.
    demands = await _build_demand_items(db, version, intent_codes)
    limits_for_week = await _week_limits_lookup(db, _planning_weeks(
        current_week, _generate_months(version.horizon_start_month, version.horizon_months), mode,
    ))
    shelf_life = await resolve_shelf_life(token)
    lines = generate_mps(
        demands, limits_for_week, shelf_life, run.safety_margin_fraction,
        lead_weeks=lead, current_week=current_week, mode=mode,
        locked=locked_weekly,
    )
    # Snapshot the demand context for the newly (re)placed, non-locked lines
    # -- same write-time contract as create_run.
    ctx = await _build_demand_context(db, version)

    await db.execute(delete(MrpMpsLine).where(MrpMpsLine.run_id == run.id))
    for line in lines:
        key = (line.material_code, line.demand_month, line.plan_week_start)
        if line.locked:
            manual_adjusted = locked_manual_adjusted.get(key, False)
            # Carried over unchanged -- don't null a locked line's already-
            # stored context just because it was re-persisted this cycle.
            demand_forecast, opening_stock = locked_demand_context.get(
                key, (Decimal("0"), Decimal("0"))
            )
        else:
            manual_adjusted = False
            demand_forecast, opening_stock = ctx.get(line.material_code, {}).get(
                line.demand_month, (Decimal("0"), Decimal("0"))
            )
        db.add(MrpMpsLine(
            run_id=run.id, material_code=line.material_code, demand_month=line.demand_month,
            plan_week_start=line.plan_week_start, plan_week_month=line.plan_week_month,
            weeks_early=line.weeks_early,
            qty=line.qty, is_prebuild=line.is_prebuild,
            prebuild_reason=line.prebuild_reason, shelf_life_ok=line.shelf_life_ok,
            capacity_gap=line.capacity_gap, locked_by_planner=line.locked,
            manual_adjusted=manual_adjusted,
            demand_forecast=demand_forecast, opening_stock=opening_stock,
            lead_shortfall=line.lead_shortfall,
        ))
    stats = _compute_stats(lines)
    stats["skipped_intent"] = _skipped_intent_stats(intent_lines)
    stats["no_shelf_life"] = await _no_shelf_life_stats(lines, shelf_life, token)
    run.stats = stats

    await db.commit()
    await db.refresh(run)
    return await _run_detail_response(db, run)


@router.patch("/runs/{run_id}/lines/{line_id}", response_model=MpsLineResponse)
async def update_line(
    run_id: uuid.UUID, line_id: uuid.UUID, body: MpsLineUpdate,
    db: SessionDep, _: RunDep, token: BearerToken,
):
    """Hand-adjust one line: change its quantity, move it to a different
    WEEK, or lock it (design §5.2's adjust drawer).

    Moving a line is not a relabelling. `plan_week_month` is recomputed from
    the new week under the RUN's own `week_calendar_mode` (the same
    ownership rule the engine placed the line with), `weeks_early` is
    recomputed against the demand month's lead-shifted target week, and the
    move is put through **the engine's own shelf-life rule**
    (`_placement_allowed`) -- imported rather than restated, because that
    function is deliberately the single consumption point of the rule and a
    second copy here would be free to drift from it.

    A move the shelf-life rule refuses is REJECTED (422), not stored with
    `shelf_life_ok=False`. The engine only ever puts that flag on a
    `capacity_gap` line -- a produced line always carries
    `shelf_life_ok=True` -- so storing a produced line that fails the rule
    would invent a row shape nothing downstream knows how to read, for
    product that expires before the month it was made for.
    """
    run = await _get_run_or_404(db, run_id)
    _require_not_released(run)
    line = await _get_line_or_404(db, run_id, line_id)
    mode = run.week_calendar_mode

    changed = False
    if body.qty is not None and body.qty != line.qty:
        line.qty = body.qty
        changed = True
    if body.plan_week_start is not None and body.plan_week_start != line.plan_week_start:
        week = body.plan_week_start
        if week_start_of(week, mode) != week:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{week.isoformat()} is not the start of a week under this run's "
                       f"'{mode}' calendar",
            )
        current_week = week_start_of(datetime.now(timezone.utc).date(), mode)
        target = _target_week(line.demand_month, run.production_lead_weeks, current_week, mode)
        weeks_early = _weeks_between(week, target, mode)
        shelf_life = await resolve_shelf_life(token)
        if not _placement_allowed(
            week, line.demand_month, shelf_life.get(line.material_code),
            run.safety_margin_fraction, weeks_early,
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"the week of {week.isoformat()} is {weeks_early} week(s) earlier than "
                       f"{line.material_code}'s target week for {line.demand_month} demand, "
                       "which its shelf life does not allow",
            )
        line.plan_week_start = week
        line.plan_week_month = owning_month(week, mode)
        line.weeks_early = weeks_early
        # Same definition the engine uses: a pre-build is production pulled
        # into an earlier MONTH than the demand's own bucket. Moving within
        # the bucket is levelling, however early in it the new week sits.
        line.is_prebuild = line.plan_week_month < owning_month(target, mode)
        changed = True
    if body.locked_by_planner is not None:
        if body.locked_by_planner and line.capacity_gap:
            # See the module docstring: the engine drops locked gap lines,
            # so allowing this would silently clear the lock (and this row's
            # manual_adjusted and demand-context snapshot) on the next
            # recalculate. Refusing is the only outcome a planner can see.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="a capacity_gap line is unmet demand, not committed production, and "
                       "cannot be locked -- resolve the gap (add capacity, or move/adjust the "
                       "line) and lock the resulting production line instead",
            )
        line.locked_by_planner = body.locked_by_planner
    if changed:
        line.manual_adjusted = True

    await db.commit()
    await db.refresh(line)
    return _line_response(line, mode)


@router.post("/runs/{run_id}/confirm-release", response_model=MpsRunDetailResponse)
async def confirm_release(run_id: uuid.UUID, db: SessionDep, _: ConfirmDep):
    run = await _get_run_or_404(db, run_id)
    _require_not_released(run)
    lines = await _load_lines(db, run.id)

    # Delete EVERY prior demand_type='mps' row, system-wide -- not scoped to
    # this run's forecast_version_id. See module docstring for why: multiple
    # forecast versions can be confirmed at once, but only one MPS lineage
    # is ever meant to be live/released at a time (design §8). Scoping this
    # delete to `run.forecast_version_id` would leave a prior release's rows
    # behind forever whenever it was built off a different version_id, and
    # silently double-count demand.
    await db.execute(delete(MrpDemand).where(MrpDemand.demand_type == "mps"))

    for line in lines:
        if line.capacity_gap:
            # Unmet-demand exception, not a booked production order -- never
            # materialized into mrp_demands (see module docstring). Still
            # persisted as an MrpMpsLine above, so it stays visible.
            continue
        db.add(MrpDemand(
            source_run_id=run.id, demand_type="mps", material_code=line.material_code,
            # demand_month here means "the month production is booked in",
            # which weekly is the plan week's owning month -- the same
            # substitution the monthly version made with plan_month.
            demand_month=line.plan_week_month, plan_week_start=line.plan_week_start,
            qty=line.qty,
        ))

    run.status = "released"
    await db.commit()
    await db.refresh(run)
    return await _run_detail_response(db, run)
