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

# Weekly bucket packing (weekly-MPS Task 4)

`pack_bucket` is the *weekly* half of this module and is deliberately kept
separate from `generate_mps` above: it packs ONE bucket (one demand month's
worth of net requirement) into that bucket's weeks. It knows nothing about
lead time, cross-bucket pre-build or shelf life -- a later task re-bases
those onto weeks and calls this function per bucket. It is a pure function
of `(items, weeks, limits)`: it receives an already-resolved `list[date]` of
week starts and never asks (or cares) which of `week_calendar.py`'s three
week modes produced them, and it receives already-resolved `CapacityLimits`
(never calls `capacity.resolve_limits_for_week` itself).

See `pack_bucket`'s own docstring for the two regimes and every tie-break.
"""
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_FLOOR, Decimal


@dataclass(frozen=True)
class DemandItem:
    material_code: str
    demand_month: str        # 'YYYY-MM'
    qty: Decimal


@dataclass(frozen=True)
class CapacityLimits:
    max_sku_count: int | None            # per month; None = unlimited
    max_output_qty: Decimal | None       # per month, KG; None = unlimited
    # SOFT floor (weekly-MPS Task 3) -- defaults None (no floor) so every
    # existing two-positional-arg call site (this module's own fits()/
    # _overflow_reason(), and app/api/v1/mps.py's still-month-based
    # _resolve_capacity_limits, deliberately left untouched by Task 3 --
    # see that task's brief) keeps working unchanged. Unlike the two fields
    # above, the placement algorithm (`fits`, `_overflow_reason`) does not
    # read this field at all: min_output_qty only ever limits how thinly a
    # later week-based scheduler may spread output across weeks, it must
    # never reject a placement or create a capacity gap the way the max_*
    # ceilings do.
    min_output_qty: Decimal | None = None


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


# ══════════════════════════════════════════════════════════════════════════
# Weekly bucket packing (weekly-MPS Task 4)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class BucketItem:
    """One material's net requirement for one demand month, to be packed
    into that bucket's weeks."""
    material_code: str
    demand_month: str        # 'YYYY-MM'
    qty: Decimal


@dataclass(frozen=True)
class WeeklyLine:
    """One material producing `qty` in the week starting `plan_week_start`.

    Field order mirrors `PlannedLine` so the two are readable side by side;
    everything from `is_prebuild` onward defaults, because single-bucket
    packing has no opinion on lead time / pre-build / shelf life -- those
    are filled in by the later task that drives `pack_bucket` per bucket.
    """
    material_code: str
    demand_month: str
    plan_week_start: date
    qty: Decimal
    is_prebuild: bool = False
    prebuild_reason: str | None = None
    shelf_life_ok: bool = True
    capacity_gap: bool = False
    locked: bool = False
    lead_shortfall: bool = False


# `mrp_mps_lines.qty` is Numeric(18, 3); level a run to the same resolution
# the row will be stored at, so the plan a planner reads back is byte-equal
# to the plan this engine computed.
_QTY_QUANTUM = Decimal("0.001")


def _tidy(qty: Decimal) -> Decimal:
    """Strip trailing zeros without changing the value ('20.000' -> '20').

    Purely cosmetic -- `Decimal('20.000') == Decimal('20')` either way, and
    every sum in this module is computed before tidying. It exists so a
    levelled run does not render as '20.000' next to a whole-week chunk
    rendering as '40'. `normalize()` alone is not enough: it turns integral
    values into exponent form (`Decimal('20.000').normalize()` is
    `Decimal('2E+1')`), hence the integral special case.
    """
    if qty == qty.to_integral_value():
        return qty.quantize(Decimal("1"))
    return qty.normalize()


def _ceil_div(qty: Decimal, divisor: Decimal) -> int:
    """ceil(qty / divisor) computed EXACTLY (both > 0).

    `int((qty / divisor).to_integral_value(ROUND_CEILING))` would go through
    a 28-significant-digit division first, so a quotient a hair under an
    integer could round up to it and inflate the answer by a whole week.
    `divmod` on `Decimal` is exact, so that cannot happen here."""
    whole, remainder = divmod(qty, divisor)
    return int(whole) + (1 if remainder > 0 else 0)


def _need_weeks(qty: Decimal, cap: Decimal | None, week_count: int) -> int:
    """Minimum number of weeks `qty` must occupy at `cap` per week.

    `cap is None` (unlimited) means any quantity fits in a single week, per
    the design's "cap 为 None 时视作 1". A non-positive `cap` means nothing
    can be produced at all: return more weeks than the bucket has, which
    forces the tight regime, where the placement loop turns the whole
    quantity into an explicit `capacity_gap` instead of dividing by zero."""
    if cap is None:
        return 1
    if cap <= 0:
        return week_count + 1
    return _ceil_div(qty, cap)


def _spread_ceiling(qty: Decimal, need: int, min_out: Decimal | None, week_count: int) -> int:
    """Most weeks `qty` may be spread over: `max(need, floor(qty / min_out), 1)`,
    clamped to the bucket's week count.

    **Capacity always wins over the floor** -- `need` is a hard physical
    minimum, `min_out` only ever says "do not thin below this", so with
    `qty=100, cap=40, min_out=50` the answer is 3 (from `need`) even though
    every one of those weeks then sits below the 50 floor. No `min_out`
    (None, or a non-positive value, which would be a division by zero and
    means "no floor configured" anyway) means no spreading beyond `need`."""
    if min_out is None or min_out <= 0:
        return min(need, week_count)
    return min(max(need, int(qty // min_out), 1), week_count)


def _level(qty: Decimal, week_count: int) -> list[Decimal]:
    """Split `qty` evenly across `week_count` weeks, **exactly**.

    The last week is computed as `qty - (everything already allocated)`
    rather than being rounded like its siblings, so the parts always sum to
    exactly `qty`. Rounding every week independently would drift by up to
    `week_count * quantum` and silently create (or destroy) product -- which
    is precisely what `test_nothing_is_silently_lost` exists to catch."""
    if week_count <= 1:
        return [qty]
    base = (qty / week_count).quantize(_QTY_QUANTUM, rounding=ROUND_DOWN)
    allocated = base * (week_count - 1)
    return [_tidy(base)] * (week_count - 1) + [_tidy(qty - allocated)]


class _WeekLoad:
    """Running SKU-count and qty ledger for one week of one bucket."""

    __slots__ = ("skus", "qty")

    def __init__(self) -> None:
        self.skus: set[str] = set()
        self.qty: Decimal = Decimal("0")

    @property
    def is_empty(self) -> bool:
        return not self.skus

    def sku_room(self, material_code: str, limits: CapacityLimits) -> bool:
        if limits.max_sku_count is None:
            return True
        count = len(self.skus) if material_code in self.skus else len(self.skus) + 1
        return count <= limits.max_sku_count

    def remaining(self, limits: CapacityLimits) -> Decimal | None:
        """Unused qty capacity this week, or None when qty is unlimited."""
        if limits.max_output_qty is None:
            return None
        return limits.max_output_qty - self.qty

    def has_room_for(self, material_code: str, qty: Decimal, limits: CapacityLimits) -> bool:
        if not self.sku_room(material_code, limits):
            return False
        remaining = self.remaining(limits)
        return remaining is None or remaining >= qty

    def commit(self, material_code: str, qty: Decimal) -> None:
        self.skus.add(material_code)
        self.qty += qty


def _sort_key(item: BucketItem) -> tuple:
    """Descending quantity, then material_code, then demand_month.

    The design says "按 q 降序（同量按 code 稳定排序）"; `demand_month` is
    appended only so that two BucketItems that are identical but for their
    demand month still order deterministically."""
    return (-item.qty, item.material_code, item.demand_month)


def _line(item: BucketItem, week: date, qty: Decimal, **kw) -> WeeklyLine:
    return WeeklyLine(
        material_code=item.material_code,
        demand_month=item.demand_month,
        plan_week_start=week,
        qty=_tidy(qty),
        **kw,
    )


def _gap_reason(qty: Decimal, limits: CapacityLimits) -> str:
    ceilings = []
    if limits.max_sku_count is not None:
        ceilings.append(f"max_sku_count {limits.max_sku_count}")
    if limits.max_output_qty is not None:
        ceilings.append(f"max_output_qty {limits.max_output_qty}")
    ceiling_text = " and ".join(ceilings) or "no capacity"
    return f"no week left in this bucket for {_tidy(qty)} under {ceiling_text}"


def _pack_tight(items: list[BucketItem], weeks: list[date], limits: CapacityLimits) -> list[WeeklyLine]:
    """Tight regime: the bucket is (at least) full, so pack, do not spread.

    `min_output_qty` deliberately plays NO part here. There is no slack to
    thin anything into, and honouring a floor would only push a product into
    an extra week it does not need -- manufacturing a capacity gap out of
    nothing. The floor is a spreading limit, never a placement rule.
    """
    cap = limits.max_output_qty
    loads = [_WeekLoad() for _ in weeks]
    lines: list[WeeklyLine] = []

    for item in sorted(items, key=_sort_key):
        code, qty = item.material_code, item.qty
        spans_weeks = cap is not None and qty > cap

        if spans_weeks:
            # Only a product that cannot physically fit in one week is
            # allowed to be split, and then it starts at the earliest week
            # with ANY room, fills whole weeks, and drops its remainder in
            # the immediately following week. Starting at the earliest
            # *empty* week instead would strand the partial week a previous
            # oversized product left behind, which in a bucket that is by
            # definition full turns spare capacity into a phantom gap.
            start = next(
                (i for i, load in enumerate(loads)
                 if load.sku_room(code, limits)
                 and (load.remaining(limits) is None or load.remaining(limits) > 0)),
                None,
            )
        else:
            # A product that fits inside one week is NEVER split (P1: a run
            # must be contiguous, and every split costs two cleandowns).
            # Prefer a whole empty week (P3: one product per week); only
            # when the calendar has no empty week left does it slot into
            # another product's leftover space. Because items are placed
            # largest-first, the products that end up sharing are the small
            # ones -- exactly the golden case's B (20) landing in W2's
            # leftover 20 after A, C and D have taken their own weeks.
            start = next(
                (i for i, load in enumerate(loads)
                 if load.is_empty and load.has_room_for(code, qty, limits)),
                None,
            )
            if start is None:
                start = next(
                    (i for i, load in enumerate(loads)
                     if load.has_room_for(code, qty, limits)),
                    None,
                )

        remaining = qty
        last_used: int | None = None
        i = start if start is not None else len(weeks)
        while remaining > 0 and i < len(weeks):
            load = loads[i]
            if not load.sku_room(code, limits):
                break                      # stop rather than hop: P1 contiguity
            room = load.remaining(limits)
            take = remaining if room is None else min(remaining, room)
            if take <= 0:
                break
            load.commit(code, take)
            lines.append(_line(item, weeks[i], take))
            remaining -= take
            last_used = i
            i += 1

        if remaining > 0:
            # Never silently drop demand: what did not fit surfaces as an
            # explicit gap line carrying the shortfall qty. Gap lines book
            # no capacity (they are a shortfall, not a production slot), and
            # are pinned to the last week this product actually occupied so
            # they stay inside its run -- or to the bucket's last week when
            # it got nowhere at all.
            gap_week = weeks[last_used] if last_used is not None else weeks[-1]
            lines.append(_line(item, gap_week, remaining,
                               capacity_gap=True,
                               prebuild_reason=_gap_reason(remaining, limits)))

    return lines


def _pack_spare(items: list[BucketItem], weeks: list[date], limits: CapacityLimits,
                needs: dict[int, int]) -> list[WeeklyLine]:
    """Spare regime: more weeks than the demand strictly needs.

    Every product starts at its physical minimum `need_weeks` and may grow
    up to `_spread_ceiling`; the leftover weeks go one at a time to whichever
    product currently carries the heaviest per-week load (P2: spread the work
    out instead of cramming the front of the month and idling the back).
    Growth stops at the `min_output_qty` floor, and **the weeks nobody can
    use stay empty** -- splitting 20 t into four 5 t weeks burns energy for
    nothing. Products are then laid out as contiguous blocks, largest first,
    from the first week onward.
    """
    week_count = len(weeks)
    ordered = sorted(items, key=_sort_key)
    assigned = {id(item): needs[id(item)] for item in ordered}
    ceilings = {
        id(item): _spread_ceiling(item.qty, needs[id(item)], limits.min_output_qty, week_count)
        for item in ordered
    }

    spare = week_count - sum(assigned.values())
    while spare > 0:
        candidates = [item for item in ordered if assigned[id(item)] < ceilings[id(item)]]
        if not candidates:
            break                          # everyone is at their floor: leave weeks empty
        # Heaviest per-week load first; ties by larger total qty, then by
        # material_code / demand_month for a fully deterministic answer.
        heaviest = min(
            candidates,
            key=lambda it: (-(it.qty / Decimal(assigned[id(it)])), -it.qty,
                            it.material_code, it.demand_month),
        )
        assigned[id(heaviest)] += 1
        spare -= 1

    lines: list[WeeklyLine] = []
    cursor = 0
    for item in ordered:
        span = assigned[id(item)]
        for offset, chunk in enumerate(_level(item.qty, span)):
            lines.append(_line(item, weeks[cursor + offset], chunk))
        cursor += span
    return lines


def pack_bucket(items: list[BucketItem], weeks: list[date], limits: CapacityLimits) -> list[WeeklyLine]:
    """Pack one bucket's net requirements into that bucket's weeks.

    Pure function. `weeks` is an already-resolved ascending list of week-start
    dates (see `week_calendar.weeks_of_month`); this function never branches
    on which week mode produced it. `limits` is already resolved for the
    bucket (see `capacity.resolve_limits_for_week`); this function never
    touches the DB.

    ## The three principles it enforces

    - **P1 -- a product's run is contiguous.** Changeovers cost a cleandown
      each way, so A-then-B-then-A is never planned. A product whose quantity
      fits within one week's capacity is never split at all; only a product
      exceeding `max_output_qty` may span weeks, and then it fills whole
      weeks and drops its remainder in the immediately following week.
    - **P2 -- when the month is not full, spread out** rather than cramming
      the first weeks and idling the plant at the end.
    - **P3 -- prefer one product per week.** Fewer changeovers again; a
      product only shares a week when the calendar has no empty week left.

    ## The two regimes

    `need_weeks(p) = ceil(qty / max_output_qty)` (1 when qty is unlimited).

    - **Tight** (`sum(need_weeks) >= len(weeks)`) -- see `_pack_tight`.
      `min_output_qty` does not apply: there is no room to thin anything.
    - **Spare** (`sum(need_weeks) < len(weeks)`) -- see `_pack_spare`.
      Empty weeks are a legitimate result.

    ## Edge cases and tie-breaks (none of which the design pinned down)

    - **Equal quantities** order by `material_code` ascending, then
      `demand_month` -- arbitrary but stable, so the same input always
      produces the same plan.
    - **A product larger than the whole bucket's capacity** fills every week
      it can reach; the unproducible remainder becomes one `capacity_gap`
      line pinned to the last week it occupied. That week therefore carries
      two lines for that material (one real, one gap). The gap books no
      capacity and is not part of the product's physical run.
    - **`qty <= 0` items are dropped** -- a zero net requirement is nothing
      to produce, and a negative one is upstream nonsense that must not be
      turned into a negative production line (nor divided by, when computing
      `need_weeks`). Nothing is lost: there was nothing to lose.
    - **A bucket with no weeks raises `ValueError`** when there is anything
      to pack. Returning `[]` would silently drop real demand, which this
      module never does. An empty `items` list with no weeks returns `[]`.
    - **`max_sku_count` blocking the leftover-packing step** (a small product
      that would fit a week's spare qty, but that week already holds the
      maximum number of SKUs) makes that week ineligible; if no week is
      eligible anywhere, the product becomes a `capacity_gap` on the
      bucket's last week rather than breaching the ceiling. A
      `max_sku_count` below 1 means no week may host any SKU at all, so
      every item becomes a gap -- including in what would otherwise be the
      spare regime, which lays out one product per week and so normally has
      no reason to consult the SKU ceiling at all.
    - **`max_output_qty <= 0`** likewise makes every item a gap (it forces
      the tight regime, where no week can accept anything), instead of
      dividing by zero in `need_weeks`.
    - **Two BucketItems with the same `material_code`** (possible once a
      later task feeds one bucket from more than one demand month) are packed
      as two independent runs. They count as ONE SKU when they share a week,
      which is physically right; but P1 contiguity is then guaranteed per
      BucketItem, not per material.
    """
    if not weeks:
        if not items:
            return []
        raise ValueError(
            "pack_bucket was given demand but no weeks to place it in; "
            "refusing to silently drop it."
        )

    payload = [item for item in items if item.qty > 0]
    if not payload:
        return []

    week_count = len(weeks)
    needs = {id(item): _need_weeks(item.qty, limits.max_output_qty, week_count)
             for item in payload}

    # `_pack_spare` lays out one product per week and therefore never needs
    # to consult `max_sku_count` -- except when that ceiling is below 1, i.e.
    # no week may host any SKU at all. Route that through `_pack_tight`,
    # whose per-week `sku_room` check turns every item into an explicit gap
    # instead of quietly breaching the ceiling.
    no_week_can_host = limits.max_sku_count is not None and limits.max_sku_count < 1

    if no_week_can_host or sum(needs.values()) >= week_count:
        lines = _pack_tight(payload, weeks, limits)
    else:
        lines = _pack_spare(payload, weeks, limits, needs)

    return sorted(lines, key=lambda l: (l.plan_week_start, l.material_code,
                                        l.demand_month, l.capacity_gap))
