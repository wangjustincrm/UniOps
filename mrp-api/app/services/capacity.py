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
    """Active FACTORY-WIDE rules whose [effective_from, effective_to]
    window covers `on_date`. "Factory-wide" means both `scope_type ==
    'factory'` AND `scope_ref IS NULL` — the two together are exactly the
    set the migration's partial unique index on `mrp_capacity_rules`-alike
    dedup logic assumes elsewhere (see `MrpCapacityException`'s docstring;
    `MrpCapacityRule` itself has no such index, which is exactly why this
    function still needs an explicit deterministic order — see below).
    `scope_type == 'factory'` alone is NOT sufficient: nothing stops a
    caller from writing `scope_type='factory'` with a non-null `scope_ref`
    (that combination is nonsensical — a factory-wide scope_type paired
    with a specific ref — but the schema doesn't forbid it), and such a row
    must never be swept into the factory-wide resolution alongside the real
    factory-wide row.

    Ordered by `(effective_from, id)` ascending so that, when more than one
    active rule of the same constraint_type happens to overlap `on_date`
    (not prevented by any DB constraint — unlike `MrpCapacityException`,
    `MrpCapacityRule` has no uniqueness guarantee at all), the
    most-recently-effective one wins in `resolve_limits_for_week`'s
    last-write-wins loop below, and ties on `effective_from` itself resolve
    to a fixed (if arbitrary) row rather than whatever order Postgres
    happens to return. That tie-break isn't exercised by any current test;
    it only matters if two overlapping standing rules of the same
    constraint_type are ever created for the same scope."""
    rows = (await db.execute(
        select(MrpCapacityRule)
        .where(
            MrpCapacityRule.is_active.is_(True),
            MrpCapacityRule.scope_type == "factory",
            MrpCapacityRule.scope_ref.is_(None),
        )
        .order_by(MrpCapacityRule.effective_from, MrpCapacityRule.id)
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
    most one active exception can exist per (week_start, factory-wide scope,
    constraint_type) — enforced by the migration's partial unique index on
    `(week_start, scope_type, constraint_type) WHERE scope_ref IS NULL`, see
    `MrpCapacityException`'s docstring — but that index only dedupes rows
    that actually satisfy `scope_ref IS NULL`. This resolver therefore
    filters on `scope_type == 'factory' AND scope_ref IS NULL` together
    (exactly the set the partial index covers), the same way
    `_factory_rules_active_on` does for standing rules, and adds an
    explicit `order_by(id)` besides — belt-and-suspenders determinism in
    case a `scope_type='factory'`-with-non-null-`scope_ref` row (nonsensical
    but not schema-forbidden) ever slips past the index's protection some
    other way.

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
        select(MrpCapacityException)
        .where(
            MrpCapacityException.week_start == week_start,
            MrpCapacityException.scope_type == "factory",
            MrpCapacityException.scope_ref.is_(None),
            MrpCapacityException.is_active.is_(True),
        )
        .order_by(MrpCapacityException.id)
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
