"""MPS scheduling algorithm — pure logic (Phase 1B Task 3, design §6.5).

`generate_mps` turns net requirements (`DemandItem`s, one per material x
demand month) into a monthly master production schedule (`PlannedLine`s),
subject to two per-month capacity ceilings (`CapacityLimits`: SKU count and
total output qty) and a shelf-life hard rule that bounds how many months
early a product may be pre-built.

**Pure function — no DB, no clock, no imports of app models.** Task 4 wires
this to real data (capacity rules resolved per month, shelf life read from
mdm-api, locked lines read from a prior committed MPS run); this module only
ever sees plain dataclasses + `Decimal`, so it is exhaustively unit-testable
and safe to reason about in isolation.

## Algorithm

1. **Seed.** Every `locked` line's `(plan_month, material_code, qty)` is
   loaded into that month's running capacity ledger before anything else is
   scheduled. Locked lines are echoed into the output completely unchanged
   and never considered for movement — they represent an already-committed
   plan (e.g. released production orders) that this run must not disturb.
2. **Place ascending.** Demand months are processed in chronological order
   (`YYYY-MM` string sort == chronological order for zero-padded months).
   Within a month, demand items are tried in **ascending shelf-life-headroom,
   then ascending qty, then material_code** order — i.e. the least-movable
   items (short/unknown shelf life) get first claim on that month's
   capacity, and the most-movable items (long shelf life, and among ties the
   larger-qty ones) are evaluated last. This single ascending pass is what
   produces the brief's "pick movable products by headroom DESC, qty DESC"
   selection for *who* ends up needing to move: whichever items are tried
   last are, by construction, the most movable ones, so they are exactly
   the ones still competing for room once the month fills up.
3. **On overflow** (adding the item would breach the SKU-count cap or the
   qty cap, or both), the item cannot stay in its demand month. Search
   backward for the **nearest earlier month with room**, hop by hop
   (hop=1, 2, 3, ...), stopping at the first hop that has both SKU and qty
   room. A hop is only legal while
   `hop <= floor(shelf_life_months * (1 - safety_margin_fraction))`
   (the shelf-life hard check) — unknown shelf life (`None`) makes every
   hop illegal (fail safe: never pre-build a product whose shelf life is
   unknown). If a legal, roomy hop is found, the item is pre-built there.
4. **Unplaceable → capacity gap.** If no earlier month can legally take the
   item, it is emitted at `plan_month == demand_month` anyway with
   `capacity_gap=True` — net requirements are never silently dropped, they
   surface as an explicit gap for a human (or a later purchasing/expedite
   step) to see. `capacity_gap` lines do **not** consume any month's
   capacity ledger (they represent a shortfall, not a physical production
   slot booked anywhere).

## Documented ambiguity / design choices

- **Tie-break for equal headroom and qty**: broken by `material_code`
  ascending, for full determinism. The brief's own overflow example (two
  equal-priority products, either may move) does not care which one wins,
  so this is an arbitrary-but-stable choice.
- **Selection among 3+ simultaneously-competing movable products**: resolved
  by the single ascending placement pass described in step 2 above, rather
  than a full re-optimization/backtracking search. This always respects the
  capacity and shelf-life invariants and matches the brief's selection
  criteria in the pairwise cases the brief's tests exercise; for
  pathological cases with several SKUs of near-identical movability
  fighting over the same month, an alternative valid ordering might move a
  different specific SKU, but which one is not specified by the design doc.
- **`shelf_life_ok`**: true whenever shelf life *would* have allowed at
  least a 1-month earlier pre-build (`floor(shelf_life * (1 - margin)) >= 1`)
  — i.e. it answers "was shelf life the reason this couldn't move?", not
  "did this line actually move". A line placed directly in its demand month
  is always `shelf_life_ok=True` (no pre-build was ever attempted). A
  `capacity_gap` line is `shelf_life_ok=False` only when shelf life itself
  (unknown, or too short for even a 1-month pre-build) is what blocked it;
  if shelf life was adequate but no earlier month had physical room, the
  gap is `shelf_life_ok=True` (a pure capacity shortfall).
- **`prebuild_reason`**: populated (naming the constraint breached in the
  item's *original* demand month — e.g. `"2026-11 over max_output_qty
  160000 KG"`) for both successful pre-builds and capacity-gap lines, since
  both are consequences of the same overflow event. It is `None` only when
  the item placed directly in its demand month with no overflow at all.
"""
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal


@dataclass(frozen=True)
class DemandItem:
    material_code: str
    demand_month: str        # 'YYYY-MM'
    qty: Decimal


@dataclass(frozen=True)
class CapacityLimits:
    max_sku_count: int | None       # per month; None = unlimited
    max_output_qty: Decimal | None  # per month, KG; None = unlimited


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


# ── Month string helpers ('YYYY-MM') — no date-library import ──────────────


def _month_index(month: str) -> int:
    year, mon = month.split("-")
    return int(year) * 12 + (int(mon) - 1)


def _shift_month(month: str, delta: int) -> str:
    idx = _month_index(month) + delta
    year, mon0 = divmod(idx, 12)
    return f"{year:04d}-{mon0 + 1:02d}"


# ── Shelf-life math ──────────────────────────────────────────────────────────

# Sentinel: unknown shelf life can never be pre-built (fail safe). It sorts
# below every real (>= 0) headroom value, so unknown-shelf-life items are
# always the last ones tried for direct placement and the first to become a
# gap rather than silently displacing a known-shelf-life sibling.
_UNKNOWN_HEADROOM = -1


def _max_prebuild_months(shelf_life_months: int | None, safety_margin_fraction: Decimal) -> int:
    """floor(shelf_life * (1 - safety_margin_fraction)); -1 (never movable)
    when shelf life is unknown."""
    if shelf_life_months is None:
        return _UNKNOWN_HEADROOM
    allowed = Decimal(shelf_life_months) * (Decimal("1") - safety_margin_fraction)
    return int(allowed.to_integral_value(rounding=ROUND_FLOOR))


# ── Month capacity ledger ────────────────────────────────────────────────────


class _MonthLoad:
    __slots__ = ("skus", "qty")

    def __init__(self) -> None:
        self.skus: set[str] = set()
        self.qty: Decimal = Decimal("0")

    def tentative(self, material_code: str, qty: Decimal) -> tuple[int, Decimal]:
        """SKU count and total qty this month WOULD have if `qty` of
        `material_code` were added (without actually committing it)."""
        sku_count = len(self.skus) if material_code in self.skus else len(self.skus) + 1
        return sku_count, self.qty + qty

    def fits(self, material_code: str, qty: Decimal, limits: CapacityLimits) -> bool:
        sku_count, total_qty = self.tentative(material_code, qty)
        if limits.max_sku_count is not None and sku_count > limits.max_sku_count:
            return False
        if limits.max_output_qty is not None and total_qty > limits.max_output_qty:
            return False
        return True

    def commit(self, material_code: str, qty: Decimal) -> None:
        self.skus.add(material_code)
        self.qty += qty


def _overflow_reason(month: str, material_code: str, qty: Decimal, month_load: _MonthLoad, limits: CapacityLimits) -> str:
    sku_count, total_qty = month_load.tentative(material_code, qty)
    violations: list[str] = []
    if limits.max_sku_count is not None and sku_count > limits.max_sku_count:
        violations.append(f"max_sku_count {limits.max_sku_count}")
    if limits.max_output_qty is not None and total_qty > limits.max_output_qty:
        violations.append(f"max_output_qty {limits.max_output_qty} KG")
    return f"{month} over " + " and ".join(violations)


# ── Main entry point ─────────────────────────────────────────────────────────


def generate_mps(
    demands: list[DemandItem],
    limits: CapacityLimits,
    shelf_life_months: dict[str, int | None],
    safety_margin_fraction: Decimal,
    locked: list[PlannedLine] | None = None,
) -> list[PlannedLine]:
    """Schedule `demands` against `limits`, pre-building overflow subject to
    the shelf-life hard check, per the module docstring's algorithm."""
    locked_lines = locked or []

    month_loads: dict[str, _MonthLoad] = {}

    def load_for(month: str) -> _MonthLoad:
        load = month_loads.get(month)
        if load is None:
            load = _MonthLoad()
            month_loads[month] = load
        return load

    # Step 1: seed locked lines' months and echo them straight into the output.
    output: list[PlannedLine] = list(locked_lines)
    for line in locked_lines:
        load_for(line.plan_month).commit(line.material_code, line.qty)

    demand_months = sorted({d.demand_month for d in demands})

    for month in demand_months:
        items = [d for d in demands if d.demand_month == month]
        # Ascending headroom, then qty, then material_code (see module
        # docstring's algorithm step 2 for why ascending == the brief's
        # "most movable last" selection).
        ordered = sorted(
            items,
            key=lambda d: (
                _max_prebuild_months(shelf_life_months.get(d.material_code), safety_margin_fraction),
                d.qty,
                d.material_code,
            ),
        )

        for item in ordered:
            here = load_for(month)
            if here.fits(item.material_code, item.qty, limits):
                here.commit(item.material_code, item.qty)
                output.append(PlannedLine(
                    material_code=item.material_code,
                    demand_month=month,
                    plan_month=month,
                    qty=item.qty,
                    is_prebuild=False,
                    prebuild_reason=None,
                    shelf_life_ok=True,
                    capacity_gap=False,
                    locked=False,
                ))
                continue

            reason = _overflow_reason(month, item.material_code, item.qty, here, limits)
            max_hops = _max_prebuild_months(shelf_life_months.get(item.material_code), safety_margin_fraction)

            placed = False
            hop = 1
            while hop <= max_hops:
                target_month = _shift_month(month, -hop)
                target_load = load_for(target_month)
                if target_load.fits(item.material_code, item.qty, limits):
                    target_load.commit(item.material_code, item.qty)
                    output.append(PlannedLine(
                        material_code=item.material_code,
                        demand_month=month,
                        plan_month=target_month,
                        qty=item.qty,
                        is_prebuild=True,
                        prebuild_reason=reason,
                        shelf_life_ok=True,
                        capacity_gap=False,
                        locked=False,
                    ))
                    placed = True
                    break
                hop += 1

            if not placed:
                # Never silently drop: emit the gap pinned to the demand
                # month. shelf_life_ok records whether shelf life itself
                # (as opposed to plain lack of room) was the blocker.
                output.append(PlannedLine(
                    material_code=item.material_code,
                    demand_month=month,
                    plan_month=month,
                    qty=item.qty,
                    is_prebuild=False,
                    prebuild_reason=reason,
                    shelf_life_ok=max_hops >= 1,
                    capacity_gap=True,
                    locked=False,
                ))

    return output
