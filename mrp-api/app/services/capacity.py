"""Capacity rule resolution (Phase 1B Task 1; weekly since the 2026-08-12
weekly-planning rework).

`resolve_limits_for_week` is the ONE resolver: active `MrpCapacityRule`
rows whose [effective_from, effective_to] window covers a given week-start
date (`effective_to=None` means open-ended), plus a same-week active
`MrpCapacityException` override per `constraint_type`.

**A month-based `resolve_effective_rules` used to live beside it** — it
took a `'YYYY-MM'` and returned the raw rule rows, and `app/api/v1/mps.py`
fed them to the month-based engine as a single whole-horizon ceiling. Both
were deleted together when `mps.py` moved onto weeks. They had to go in the
same change: a monthly ceiling left reachable behind a "weekly" API keeps
feeding the engine a number roughly 4x the real weekly capacity while
looking replaced, which is precisely what migration `mrp10b` deactivates
the standing rules to prevent.

Scoped to `scope_type == 'factory'` AND `scope_ref IS NULL` only — Phase
1B/1C only ever write factory-wide rows; product_family/line scoping stays
a schema-level allowance for later, not something this resolver consumes
yet.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capacity import MrpCapacityException, MrpCapacityRule
from app.services.mps_engine import CapacityLimits
from app.services.week_calendar import week_start_of

_OUTPUT_QTY_AND_SKU_TYPES = ("max_sku_count", "max_output_qty", "min_output_qty")


PRODUCT_SCOPE = "product"


async def resolve_min_lots(db: AsyncSession, week_start: date) -> dict[str, Decimal]:
    """Per-product minimum lot sizes in force during `week_start`.

    "Minimum lot size" is the business rule *open the line at all and you
    make at least this much* — running less burns a changeover and a
    cleandown to produce a token quantity. It differs per product (pack
    size, recipe, line speed), which is why it is a `scope_type='product'`
    rule keyed by `scope_ref=<material_code>` rather than the one
    factory-wide number.

    Only products with their OWN rule appear here. Everything else falls
    back to `resolve_default_min_lot` (the factory-wide `min_output_qty`),
    and a product with neither has no floor at all — the pre-existing
    behaviour, so an unconfigured factory plans exactly as it does today.

    Deliberately NOT folded into `resolve_limits_for_week`: that resolver
    returns per-week ceilings scoped strictly to `scope_type='factory' AND
    scope_ref IS NULL`, a filter its partial unique index depends on.
    Product rows would break that guarantee.
    """
    rows = (await db.execute(
        select(MrpCapacityRule)
        .where(
            MrpCapacityRule.scope_type == PRODUCT_SCOPE,
            MrpCapacityRule.scope_ref.is_not(None),
            MrpCapacityRule.constraint_type == "min_output_qty",
            MrpCapacityRule.is_active.is_(True),
            MrpCapacityRule.effective_from <= week_start,
        )
        .order_by(MrpCapacityRule.id)
    )).scalars().all()
    lots: dict[str, Decimal] = {}
    for rule in rows:
        if rule.effective_to is not None and rule.effective_to < week_start:
            continue
        # Last one wins, ordered by id -- the same deterministic tie-break
        # `_factory_rules_active_on` uses for overlapping factory rules.
        lots[rule.scope_ref] = rule.limit_value
    return lots


async def resolve_default_min_lot(db: AsyncSession, week_start: date) -> Decimal | None:
    """The factory-wide `min_output_qty` in force during `week_start`, i.e.
    the floor for products with no rule of their own. `None` means no floor
    is configured and the engine may spread output as thin as capacity
    allows (the behaviour before minimum lot sizes existed)."""
    for rule in await _factory_rules_active_on(db, week_start):
        if rule.constraint_type == "min_output_qty":
            return rule.limit_value
    return None


class ExceptionShiftConflict(Exception):
    """Two exceptions would land on the same week after a grid change.

    Carries the colliding week-start dates so the caller can name them; the
    shift writes nothing when this is raised."""

    def __init__(self, weeks: list[date]):
        self.weeks = weeks
        super().__init__(
            "capacity exceptions would collide on "
            + ", ".join(w.isoformat() for w in weeks)
        )


async def shift_capacity_exceptions(
    db: AsyncSession, *, mode: str, old_dow: int, new_dow: int
) -> int:
    """Move every capacity exception onto the week grid `new_dow` produces.

    Exceptions (a maintenance shutdown, typically) are keyed by
    `week_start`. Changing the week start day shifts the entire grid, so a
    row keyed to a Monday no longer equals any week the planner is looking
    at — `resolve_limits_for_week` matches on an exact date, so the shutdown
    **silently stops applying** and the plan quietly schedules production
    into a week the plant is closed. Each row therefore moves to the
    new-grid week that CONTAINS its old start date.

    Raises `ExceptionShiftConflict` — writing nothing — when two rows would
    end up on the same `(week_start, scope_type, scope_ref,
    constraint_type)`. Silently dropping one of them is exactly the failure
    this function exists to prevent, and the table's unique constraints
    would reject the write anyway; refusing up front lets the caller name
    the weeks a human has to reconcile.

    Does not commit: the caller owns the transaction, so the parameter
    write and this shift land together or not at all.
    """
    if old_dow == new_dow:
        return 0

    rows = (await db.execute(select(MrpCapacityException))).scalars().all()
    targets: dict[int, date] = {}
    for row in rows:
        # `week_start_of` is mode-aware: under `month_fixed` the grid does
        # not depend on the start day at all, so nothing moves.
        targets[id(row)] = week_start_of(row.week_start, mode, start_dow=new_dow)

    seen: dict[tuple, int] = {}
    collisions: set[date] = set()
    for row in rows:
        key = (targets[id(row)], row.scope_type, row.scope_ref, row.constraint_type)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            collisions.add(targets[id(row)])
    if collisions:
        raise ExceptionShiftConflict(sorted(collisions))

    moved = 0
    for row in rows:
        target = targets[id(row)]
        if target != row.week_start:
            row.week_start = target
            moved += 1
    return moved


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
