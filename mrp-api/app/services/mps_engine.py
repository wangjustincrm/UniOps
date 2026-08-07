"""MPS scheduling algorithm — pure logic (Phase 1B Task 3, design §6.5; lead
time added 2026-08-07).

`generate_mps` turns net requirements (`DemandItem`s, one per material x
demand month) into a monthly master production schedule (`PlannedLine`s),
subject to two per-month capacity ceilings (`CapacityLimits`: SKU count and
total output qty), a production lead time (`lead_months`) that shifts the
default placement earlier than the demand month, and a shelf-life hard rule
that bounds how many months early a product may be pre-built (measured from
the demand month, not from the lead-adjusted target).

**Pure function — no DB, no clock, no imports of app models.** Task 4 wires
this to real data (capacity rules resolved per month, shelf life read from
mdm-api, locked lines read from a prior committed MPS run); this module only
ever sees plain dataclasses + `Decimal`, so it is exhaustively unit-testable
and safe to reason about in isolation. `current_month` (the "now" the caller
is scheduling from) is a plain parameter for the same reason — the engine
never reads a clock.

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
3. **Lead-adjusted target.** Each item's default placement is no longer its
   demand month `D` — it is `standard_target = D - lead_months`, clamped to
   never fall before `current_month` (see "Production lead time" below).
   `target` is tried first, exactly as `D` was tried first pre-lead.
4. **On overflow** (adding the item would breach the SKU-count cap or the
   qty cap, or both) at `target`, the item cannot stay there. Search
   backward for the **nearest earlier month with room**, hop by hop
   (hop=1, 2, 3, ... from `target`), stopping at the first hop that has both
   SKU and qty room. A hop to month `M` is only legal while both:
   - `M >= current_month` (never schedule production in the past), and
   - `_month_index(D) - _month_index(M) <= floor(shelf_life_months * (1 -
     safety_margin_fraction))` (the shelf-life hard check, on the TOTAL
     offset from the demand month — not from `target`) — unknown shelf life
     (`None`) makes every hop with a positive offset illegal (fail safe:
     never pre-build a product whose shelf life is unknown). A month with
     zero offset from `D` (i.e. `M == D`) is always legal regardless of
     shelf life, matching pre-lead behaviour where placing in the demand
     month itself never needed a shelf-life check.
   If a legal, roomy hop is found, the item is pre-built there.
5. **Unplaceable → capacity gap.** If `target` itself is already illegal per
   the shelf-life hard check (the lead demands more pre-build than shelf
   life allows), or no earlier legal month has room, the item is emitted at
   `plan_month == target` anyway with `capacity_gap=True` — net requirements
   are never silently dropped, they surface as an explicit gap for a human
   (or a later purchasing/expedite step) to see. `capacity_gap` lines do
   **not** consume any month's capacity ledger (they represent a shortfall,
   not a physical production slot booked anywhere).

## Production lead time

- **`standard_target = _shift_month(D, -lead_months)`** — where the item
  would be placed by default, ignoring capacity, if there were no "don't
  schedule in the past" floor.
- **`target = standard_target`, unless that falls before `current_month`, in
  which case `target = current_month`** — production is never scheduled
  before "now".
- **`lead_shortfall = target > standard_target`** (as month indices) — true
  exactly when the clamp to `current_month` fired, i.e. the lead time calls
  for production to have already started. It is a signal, independent of
  `capacity_gap`, that the plan cannot fully honour the configured lead for
  this item (there isn't enough runway between now and the demand month).
- **`is_prebuild = plan_month < standard_target`** — note this is relative
  to the *lead-adjusted* standard target, not to `D`: placing an item
  exactly at its lead-driven `target` (the normal, no-overflow case) is
  **not** a pre-build; only overflow pushing it earlier than `target` (or,
  equivalently, earlier than `standard_target`) counts as one.
- **`lead_months=0` reproduces the exact pre-lead behaviour** for any demand
  month `>= current_month`: `standard_target == D`, so `target == D` (no
  clamp), `lead_shortfall` is always `False`, and the placement/overflow/gap
  logic collapses to exactly the pre-lead algorithm.
- **Locked lines** are seeded and echoed unchanged as before; they carry
  `lead_shortfall=False` (the `PlannedLine` field default) unless the caller
  explicitly set it on the line it passed in.

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
  "did this line actually move". A line placed directly at `target` (no
  overflow) is always `shelf_life_ok=True`. A `capacity_gap` line is
  `shelf_life_ok=False` when shelf life itself is what blocked it — either
  because the material's shelf life is unknown/too short for even a
  1-month pre-build (`max_hops < 1`), **or** because the lead alone already
  demands more pre-build than shelf life allows (`target` fails the
  shelf-life hard check before any capacity search is even attempted). If
  shelf life was adequate but no earlier legal month had physical room, the
  gap is `shelf_life_ok=True` (a pure capacity shortfall) — this also
  covers hitting the `current_month` floor before the shelf-life limit.
- **`prebuild_reason`**: populated (naming the constraint breached at
  `target` — e.g. `"2026-11 over max_output_qty 160000 KG"` — or, when the
  lead itself violates the shelf-life hard check before capacity is even
  checked, a lead/shelf-life-shortfall message) for both successful
  pre-builds and capacity-gap lines, since both are consequences of the
  same "couldn't stay at `target`" event. It is `None` only when the item
  placed directly at `target` with no overflow at all. (With `lead_months
  == 0` and no clamp, `target == D`, so this collapses to the pre-lead
  "original demand month" wording.)
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
    lead_shortfall: bool = False


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
    lead_months: int,
    current_month: str,
    locked: list[PlannedLine] | None = None,
) -> list[PlannedLine]:
    """Schedule `demands` against `limits`, pre-building overflow subject to
    the shelf-life hard check, per the module docstring's algorithm.

    `lead_months` shifts every demand's default placement earlier by that
    many months (`standard_target`), clamped to never fall before
    `current_month`; see the module docstring's "Production lead time"
    section for the full behaviour and flag semantics."""
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

    current_idx = _month_index(current_month)
    demand_months = sorted({d.demand_month for d in demands})

    for month in demand_months:
        demand_idx = _month_index(month)
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
            max_hops = _max_prebuild_months(shelf_life_months.get(item.material_code), safety_margin_fraction)

            # Lead time: default placement is `lead_months` before the
            # demand month, never before `current_month`.
            standard_target = _shift_month(month, -lead_months)
            standard_target_idx = _month_index(standard_target)
            if standard_target_idx >= current_idx:
                target, target_idx = standard_target, standard_target_idx
            else:
                target, target_idx = current_month, current_idx
            lead_shortfall = target_idx > standard_target_idx

            def legal(candidate_idx: int, _demand_idx: int = demand_idx, _max_hops: int = max_hops) -> bool:
                # The shelf-life hard check is on the TOTAL offset from the
                # demand month, not just the offset from `target` — a demand
                # month itself (offset 0) is always legal regardless of
                # shelf life (no early production risk there).
                offset = _demand_idx - candidate_idx
                return offset <= 0 or offset <= _max_hops

            here = load_for(target)
            if legal(target_idx) and here.fits(item.material_code, item.qty, limits):
                here.commit(item.material_code, item.qty)
                output.append(PlannedLine(
                    material_code=item.material_code,
                    demand_month=month,
                    plan_month=target,
                    qty=item.qty,
                    is_prebuild=target_idx < standard_target_idx,
                    prebuild_reason=None,
                    shelf_life_ok=True,
                    capacity_gap=False,
                    locked=False,
                    lead_shortfall=lead_shortfall,
                ))
                continue

            if legal(target_idx):
                reason = _overflow_reason(target, item.material_code, item.qty, here, limits)
            else:
                # The lead itself already demands more pre-build than shelf
                # life allows -- searching earlier only makes the offset
                # worse, so there is no point trying.
                reason = (
                    f"{month} lead requires producing by {target} "
                    f"({demand_idx - target_idx} months early), exceeding the shelf-life "
                    "pre-build limit"
                )

            placed = False
            if legal(target_idx):
                # Pre-build search, re-based from `target` with the floor at
                # `current_month` (not the demand month).
                hop = 1
                while True:
                    candidate_idx = target_idx - hop
                    if candidate_idx < current_idx or not legal(candidate_idx):
                        break
                    candidate_month = _shift_month(target, -hop)
                    candidate_load = load_for(candidate_month)
                    if candidate_load.fits(item.material_code, item.qty, limits):
                        candidate_load.commit(item.material_code, item.qty)
                        output.append(PlannedLine(
                            material_code=item.material_code,
                            demand_month=month,
                            plan_month=candidate_month,
                            qty=item.qty,
                            is_prebuild=candidate_idx < standard_target_idx,
                            prebuild_reason=reason,
                            shelf_life_ok=True,
                            capacity_gap=False,
                            locked=False,
                            lead_shortfall=lead_shortfall,
                        ))
                        placed = True
                        break
                    hop += 1

            if not placed:
                # Never silently drop: emit the gap pinned to `target` (the
                # lead-adjusted, current-month-clamped placement). shelf_life_ok
                # records whether shelf life itself (as opposed to plain lack
                # of room, or the current-month floor) was the blocker.
                output.append(PlannedLine(
                    material_code=item.material_code,
                    demand_month=month,
                    plan_month=target,
                    qty=item.qty,
                    is_prebuild=False,
                    prebuild_reason=reason,
                    shelf_life_ok=(max_hops >= 1) if legal(target_idx) else False,
                    capacity_gap=True,
                    locked=False,
                    lead_shortfall=lead_shortfall,
                ))

    return output
