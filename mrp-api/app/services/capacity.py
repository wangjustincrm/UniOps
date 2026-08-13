"""Capacity rule resolution (Phase 1B Task 1; weekly resolver added by the
weekly-MPS Task 3).

`resolve_effective_rules` is what the (still month-based, Task-4-era) MPS
algorithm calls to find out how much factory capacity applies to a given
planning month — active rules only, filtered to those whose
[effective_from, effective_to] window covers the first day of that month.
`effective_to=None` means open-ended.

**Deliberately left untouched by Task 3**: `app/api/v1/mps.py` still calls
`resolve_effective_rules` at its own module's lines 106/333/343. A later
task rewires `mps.py` onto `resolve_limits_for_week` below and deletes this
function in the same change; retiring it now would leave the engine without
limits mid-plan, and rewiring `mps.py` now would collide with that task's
own changes to the same lines. `resolve_limits_for_week` is added *beside*
it, not in place of it.

`resolve_limits_for_week` is the week-based replacement: same idea (active
rules whose window covers a given date), plus a same-week active
`MrpCapacityException` override per `constraint_type`. Both resolvers are
scoped to `scope_type == 'factory'` only, mirroring `mps.py`'s own
`_resolve_capacity_limits` comment — Phase 1B/1C only ever write
factory-wide rows; product_family/line scoping stays a schema-level
allowance for later, not something either resolver consumes yet.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capacity import MrpCapacityException, MrpCapacityRule
from app.services.mps_engine import CapacityLimits

_OUTPUT_QTY_AND_SKU_TYPES = ("max_sku_count", "max_output_qty", "min_output_qty")


def _month_first_day(month: str) -> date:
    y, m = month.split("-")
    return date(int(y), int(m), 1)


async def resolve_effective_rules(db: AsyncSession, on_month: str) -> list[MrpCapacityRule]:
    """Active rules whose [effective_from, effective_to] window covers the
    first day of `on_month` ('YYYY-MM'). effective_to NULL = open-ended."""
    d = _month_first_day(on_month)
    rows = (await db.execute(select(MrpCapacityRule).where(MrpCapacityRule.is_active.is_(True)))).scalars().all()
    return [r for r in rows if r.effective_from <= d and (r.effective_to is None or r.effective_to >= d)]


async def _factory_rules_active_on(db: AsyncSession, on_date: date) -> list[MrpCapacityRule]:
    """Active factory-scoped rules whose [effective_from, effective_to]
    window covers `on_date`. Ordered by effective_from ascending so that,
    when more than one active rule of the same constraint_type happens to
    overlap `on_date` (not prevented by any DB constraint — MrpCapacityRule
    has no uniqueness guarantee the way MrpCapacityException does), the
    most-recently-effective one wins in `resolve_limits_for_week`'s
    last-write-wins loop below. That tie-break isn't exercised by any
    current test; it only matters if two overlapping standing rules of the
    same constraint_type are ever created for the same scope."""
    rows = (await db.execute(
        select(MrpCapacityRule)
        .where(MrpCapacityRule.is_active.is_(True), MrpCapacityRule.scope_type == "factory")
        .order_by(MrpCapacityRule.effective_from)
    )).scalars().all()
    return [r for r in rows if r.effective_from <= on_date and (r.effective_to is None or r.effective_to >= on_date)]


async def resolve_limits_for_week(db: AsyncSession, week_start: date) -> CapacityLimits:
    """Effective capacity limits for the week starting `week_start` (an
    already-resolved week-start date — this resolver has no opinion on
    which of `week_calendar.py`'s three week modes produced it; that
    module is the only place allowed to know the difference).

    Standing `MrpCapacityRule` rows active on `week_start`, with any active
    `MrpCapacityException` row for that exact `week_start` overriding the
    standing value per `constraint_type` (exceptions carry no window of
    their own — they apply to exactly the one week they're keyed to). At
    most one active exception can exist per (week_start, factory scope,
    constraint_type) — enforced by the migration's partial unique index,
    see `MrpCapacityException`'s docstring — so this never needs its own
    tie-break the way `_factory_rules_active_on` does for standing rules.

    `min_output_qty` is carried through like the other two but is a SOFT
    floor at the caller (the week-based scheduler, added by a later task):
    it must only ever narrow how thinly output may be spread across weeks,
    never cause a capacity gap or a rejected plan. Nothing in this resolver
    enforces min <= max at read time — that invariant is checked at
    *write* time by `app/api/v1/capacity.py`'s rule CRUD (deliberately NOT
    by the exception CRUD; see that module's docstring for why the two
    differ)."""
    values: dict[str, Decimal | None] = {t: None for t in _OUTPUT_QTY_AND_SKU_TYPES}
    for rule in await _factory_rules_active_on(db, week_start):
        if rule.constraint_type in values:
            values[rule.constraint_type] = rule.limit_value

    exceptions = (await db.execute(
        select(MrpCapacityException).where(
            MrpCapacityException.week_start == week_start,
            MrpCapacityException.scope_type == "factory",
            MrpCapacityException.is_active.is_(True),
        )
    )).scalars().all()
    for exc in exceptions:
        if exc.constraint_type in values:
            values[exc.constraint_type] = exc.limit_value

    max_sku_count = int(values["max_sku_count"]) if values["max_sku_count"] is not None else None
    return CapacityLimits(
        max_sku_count=max_sku_count,
        max_output_qty=values["max_output_qty"],
        min_output_qty=values["min_output_qty"],
    )
