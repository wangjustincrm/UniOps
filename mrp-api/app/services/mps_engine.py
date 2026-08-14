"""MPS scheduling algorithm — pure logic (design §2.0; weekly since the
2026-08-12 weekly-planning rework).

`generate_mps` turns net requirements (`DemandItem`s, one per material x
demand month) into a WEEKLY master production schedule (`WeeklyLine`s),
subject to per-week capacity ceilings (`CapacityLimits`: SKU count, total
output qty, and a soft output floor), a production lead time in weeks
(`lead_weeks`) that shifts the default placement earlier than the demand
month, and a shelf-life hard rule — measured in real calendar days — that
bounds how early a product may be pre-built.

**Pure function — no DB, no clock, no imports of app models.**
`app/api/v1/mps.py` wires it to real data (capacity resolved per week by
`app/services/capacity.py::resolve_limits_for_week`, shelf life from
mdm-api, locked lines from the run's own stored rows); this module only ever
sees plain dataclasses, `Decimal` and `date`, so it is exhaustively
unit-testable and safe to reason about in isolation. `current_week` (the
"now" the caller is scheduling from) is a plain parameter for the same
reason — the engine never reads a clock.

The module has two layers, in this order below:

1. `pack_bucket` — lays ONE bucket (one owning month's weeks) out.
2. `generate_mps` — the pipeline that drives it: lead shift, bucketing,
   packing, cross-bucket backward overflow, shelf-life gate, final gaps.

**A month-based engine used to live here** (`generate_monthly_mps`, plus a
`PlannedLine` dataclass and a keyword-routing `generate_mps` shim over the
two). It was deleted when `app/api/v1/mps.py` moved onto weeks: two engines
on two calendars behind one name is exactly the ambiguity the shim's
`TypeError` existed to police, and keeping a monthly ceiling reachable is
how a "weekly" plan quietly gets planned per month. Design D9 (destructive
migration `mrp10b`) means there is no monthly data left for it to serve
either.

# Weekly bucket packing (weekly-MPS Task 4)

`pack_bucket` packs ONE bucket (one demand month's worth of net
requirement) into that bucket's weeks. It knows nothing about lead time,
cross-bucket pre-build or shelf life -- `generate_mps` re-bases those onto
weeks and calls this function per bucket. It is a pure function of
`(items, weeks, limits)`: it receives an already-resolved `list[date]` of
week starts and never asks (or cares) which of `week_calendar.py`'s three
week modes produced them, and it receives already-resolved `CapacityLimits`
-- one for the whole bucket, or one PER WEEK, which is the shape
`capacity.resolve_limits_for_week` actually produces (it never calls that
resolver itself). Per-week limits are what makes "week 32 is down for
maintenance" expressible: such a week is closed, stepped over as a
placement target, and its production goes to the other weeks.

**`capacity_gap` on a `WeeklyLine` means "did not fit in THIS bucket", and
is NOT the final shortfall the design describes.** Design §2.0 step ⑤ has
quantity that will not fit overflow BACKWARDS into earlier weeks
(pre-build) first; only running into the current week or the shelf-life
limit makes a shortfall final. `pack_bucket` only ever sees one bucket and
cannot perform that search, so its gap lines are the *input* to the
cross-bucket pre-build step, not its output. `generate_mps` resolves them
before anything is persisted -- storing them raw would show a planner a
permanent shortfall for demand that could have been pre-built.

## KNOWN LIMITATION -- a de-rated week suppresses levelling

`need_weeks` is sized off the SMALLEST open week's `max_output_qty` (see
`_reference_cap`). That is the safe side -- it cannot over-produce, and it
makes the spare regime's levelling provably unable to overfill any week --
but it is blunt: **one merely de-rated week (not shut, just smaller) drags
the WHOLE bucket into the tight regime and permanently suppresses P2's
levelling.** With `caps=[40, 10, 40, 40]` and `{A: 60, B: 20}`:

    W1 A40 | W2 A10 | W3 A10 | W4 B20

-- exactly the front-loaded shape the `>` regime predicate was introduced to
eliminate, because `ref_cap=10` inflates `sum(need_weeks)` to 8 against 4
weeks. A shutdown week is NOT affected (closed weeks are excluded from the
open set before `ref_cap` is taken), so this only bites on genuine de-rating.

The cheaper alternative -- take the regime off the largest cap and have
`_pack_spare` clamp each chunk to its own slot's cap -- touches the
trickiest function here and was deliberately deferred. Do not "discover"
this again: it is a known, accepted trade.

See `pack_bucket`'s own docstring for the two regimes and every tie-break.

# Weekly pipeline (weekly-MPS Task 5)

`generate_mps` is what drives `pack_bucket`: it applies the
production lead time in WEEKS, buckets the lead-shifted demand by the
owning month of its target week, packs each bucket, and overflows whatever
does not fit backwards week by week -- across bucket boundaries -- with a
shelf-life gate on every step. Whatever survives to the current week, or
fails that gate, becomes an explicit `capacity_gap`; nothing is ever
dropped. Its docstring carries the full pipeline, why a bucket's canvas is
its owning month's FULL 4/5 weeks (and what truncating it to start at the
target week cost, which is why that was reverted), and why two demand
months of one material are merged before packing.

**What the whole-month canvas costs: just-in-time placement.** The packer
lays out from the earliest open week, so with `lead_weeks=0` a single 30 t
October demand lands in the FIRST week of its bucket (2026-09-28) where
the earlier truncated canvas put it in the last (2026-10-26). That is
exactly reproducible; the aggregate is not a single number, because it
depends on the scenario mix -- two independent sweeps of 4,000 seeds put
the rise in qty-weighted mean `weeks_early` at 1.388 -> 1.716 (review's
mix) and 0.506 -> 1.049 (a narrower mix without shutdowns or unknown
shelf life). Both agree on the direction and on roughly half a week.

The extra holding time is bounded by the shelf-life gate and by the
demand's own month, and it is what design §2.0 ④ asks for. But note that
NO field warns on it: by the round-2 ruling `is_prebuild` is False by
construction anywhere inside the bucket, so only `weeks_early` shows it.

The shelf-life gate (`_prebuild_allowed` + `_minus_months`) compares REAL
calendar-day differences, and applies to EVERY placed week earlier than
its own target, not only to the backward-overflow step (spec `dc9a78e`).
Design §2.6 forbids "4.33 weeks per month" and "30 days per month"
outright: three months ending 2026-05-01 is 89 days, not 90.93, and at a
1/3 safety margin that is the difference between allowing and refusing the
week of 2026-03-02 -- one whole week of production either shipped or
written off. The deviation itself never reaches a week (4.42 days worst
case over 18 months); it does not need to.

"""
from calendar import monthrange
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from app.services.week_calendar import (
    owning_month, shift_weeks, week_start_of, weeks_of_month,
)


@dataclass(frozen=True)
class DemandItem:
    material_code: str
    demand_month: str        # 'YYYY-MM'
    qty: Decimal


@dataclass(frozen=True)
class CapacityLimits:
    max_sku_count: int | None            # per week; None = unlimited
    max_output_qty: Decimal | None       # per week, KG; None = unlimited
    # SOFT floor (weekly-MPS Task 3) -- defaults None (no floor) so a
    # two-positional-arg construction (the tests' shorthand) keeps working.
    # Unlike the two ceilings above, min_output_qty never rejects a
    # placement or creates a capacity gap: it only ever limits how thinly
    # `_pack_spare` may spread one product's output across weeks.
    min_output_qty: Decimal | None = None


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

    Everything from `plan_week_month` onward defaults, because
    single-bucket packing has no opinion on the owning month, lead time,
    pre-build or shelf life -- `generate_mps` fills those in as it drives
    `pack_bucket` per bucket.

    **`capacity_gap` here means "did not fit in THIS bucket" -- it is not
    the design's final shortfall.** See `pack_bucket`'s docstring section
    "What a capacity_gap line means at this layer" before persisting one.
    """
    material_code: str
    demand_month: str
    plan_week_start: date
    qty: Decimal
    # Filled in by `generate_mps`, not by `pack_bucket`: the owning
    # month of `plan_week_start` depends on the week mode, and `pack_bucket`
    # is deliberately mode-blind (it only ever receives a resolved list of
    # week starts). `is_prebuild` / `weeks_early` likewise need the
    # lead-shifted target week and the demand's bucket, neither of which
    # single-bucket packing ever sees.
    plan_week_month: str | None = None
    # **`is_prebuild` and `weeks_early` answer different questions and do
    # NOT track each other.**
    #
    # `is_prebuild` -- was this production pulled into a month EARLIER than
    # the one the demand was bucketed into? That is the only thing worth
    # warning a planner about: stock made in a month it was not planned for,
    # sitting in a warehouse waiting. Producing early WITHIN the demand's own
    # bucket month is ordinary levelling -- spreading across the month is the
    # whole reason the canvas is the whole month -- and is not flagged.
    # Measured: flagging it made 87% of the lines of a gap-free plan read
    # "pre-built" at `lead_weeks=0` (58% at 4) on a plan containing zero
    # cross-bucket movement. A warning on 87% of lines is wallpaper.
    #
    # `weeks_early` -- how many whole weeks earlier than its lead-shifted
    # target week the line landed, levelling included. It stays a plain
    # distance because that is what the shelf-life gate measures (how long
    # the stock must survive) and what a detail view wants to show. So a
    # levelled line routinely carries `weeks_early > 0` with
    # `is_prebuild=False`; that pairing is correct, not a bug.
    is_prebuild: bool = False
    weeks_early: int = 0
    prebuild_reason: str | None = None
    shelf_life_ok: bool = True
    capacity_gap: bool = False
    locked: bool = False
    lead_shortfall: bool = False
    # This week runs BELOW the product's minimum lot size, because weekly
    # capacity cannot reach the lot in a single week (21 t of demand, a 20 t
    # week and a 20 t lot: two weeks of 10.5 is the only way to make it at
    # all). Capacity wins over the floor -- the alternative, rounding up to
    # two whole lots, invents nearly double the demand. This is the ONE
    # sanctioned exception to "produce 0 or at least a lot".
    below_min_lot: bool = False
    # How much of `qty` exceeds the net requirement because the batch was
    # rounded up to the product's minimum lot size. Real production: it is
    # released to 1C and its materials get purchased. It also offsets later
    # months' requirements (see `generate_mps`'s carry).
    surplus_qty: Decimal = Decimal("0")
    # Scheduled in a week LATER than the demand month, because neither that
    # month nor any earlier week could host a whole lot. The goods arrive
    # after they were needed -- amber, not red: it is still better than not
    # producing at all, but a planner has to see it.
    late_production: bool = False


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


def _week_can_host(limits: CapacityLimits) -> bool:
    """Whether this week can host ANY production at all.

    A week with `max_output_qty <= 0` (the flagship example: "week 32 is
    down for maintenance") or `max_sku_count < 1` is **closed**: it is not a
    placement target for anybody. Closed weeks are skipped, not gapped --
    a shutdown moves production to other weeks, it does not destroy it."""
    if limits.max_output_qty is not None and limits.max_output_qty <= 0:
        return False
    if limits.max_sku_count is not None and limits.max_sku_count < 1:
        return False
    return True


def _reference_cap(per_week: list[CapacityLimits], open_weeks: list[int]) -> Decimal | None:
    """The weekly output cap used to size `need_weeks` for the regime test.

    The **minimum** cap across the open weeks, not the maximum. Two reasons,
    both about staying on the safe side when the weeks are not uniform:
    `need_weeks` then over- rather than under-estimates how full the bucket
    is, so a heterogeneous bucket tips into the tight regime, which honours
    each week's own ceiling exactly; and it makes the spare regime's
    levelling provably safe, since every product gets at least
    `ceil(qty / min_cap)` weeks and therefore never puts more than the
    smallest open week's cap into any single week.

    **This is not free.** Taking the minimum is what makes a merely
    DE-RATED week drag the whole bucket into the tight regime and suppress
    levelling -- see this module's `## KNOWN LIMITATION -- a de-rated week
    suppresses levelling` section for the worked example, the reason it was
    accepted, and the cheaper alternative that was deferred. Do not change
    this function without reading it.

    `None` (unlimited) only when EVERY open week is unlimited."""
    known = [per_week[i].max_output_qty for i in open_weeks
             if per_week[i].max_output_qty is not None]
    return min(known) if known else None


def _need_weeks(qty: Decimal, ref_cap: Decimal | None) -> int:
    """Minimum number of weeks `qty` must occupy at `ref_cap` per week.
    `ref_cap is None` (unlimited) means one week, per the design's
    "cap 为 None 时视作 1"."""
    if ref_cap is None:
        return 1
    return _ceil_div(qty, ref_cap)


def _fits_in_one_week(qty: Decimal, per_week: list[CapacityLimits], open_weeks: list[int]) -> bool:
    """Whether SOME open week could hold `qty` whole (were it empty).

    Deliberately the **maximum** cap, the mirror image of `_reference_cap`'s
    minimum: this one decides whether P1 allows the product to be split at
    all, and a product that some week could take whole must never be cut up
    just because a different week is smaller."""
    return any(per_week[i].max_output_qty is None or per_week[i].max_output_qty >= qty
               for i in open_weeks)


def _spread_ceiling(qty: Decimal, need: int, min_out: Decimal | None, slot_count: int) -> int:
    """Most weeks `qty` may be spread over: `max(need, floor(qty / min_out), 1)`,
    clamped to the number of open weeks.

    **Capacity always wins over the floor** -- `need` is a hard physical
    minimum, `min_out` only ever says "do not thin below this", so with
    `qty=100, cap=40, min_out=50` the answer is 3 (from `need`) even though
    every one of those weeks then sits below the 50 floor. No `min_out`
    (None, or a non-positive value, which would be a division by zero and
    means "no floor configured" anyway) means no spreading beyond `need`."""
    if min_out is None or min_out <= 0:
        return min(need, slot_count)
    return min(max(need, int(qty // min_out), 1), slot_count)


def _level(qty: Decimal, span: int) -> list[Decimal]:
    """Split `qty` evenly across `span` weeks, **exactly**.

    The last week is computed as `qty - (everything already allocated)`
    rather than being rounded like its siblings, so the parts always sum to
    exactly `qty`. Rounding every week independently would drift by up to
    `span * quantum` and silently create (or destroy) product -- which is
    precisely what `test_nothing_is_silently_lost` exists to catch.

    `base` rounds **up**, so the tail absorbs a NEGATIVE remainder and can
    only ever come out at or below `base`. Rounding down instead would push
    the tail above its siblings and, at the wrong quantity, above the
    weekly cap itself: `qty=119.999` over 3 weeks at `cap=40` gives
    `base=39.999` and a tail of `40.001` -- one thousandth of a kilo over a
    hard ceiling, from nothing but a rounding choice."""
    if span <= 1:
        return [qty]
    base = (qty / span).quantize(_QTY_QUANTUM, rounding=ROUND_UP)
    tail = qty - base * (span - 1)
    if tail < 0:
        # Only reachable at absurd inputs (a per-week share smaller than
        # `(span - 1)` thousandths), where rounding up overshoots the whole
        # quantity. Rounding down cannot go negative, and cannot overshoot
        # the cap either -- that risk lives at the opposite extreme.
        base = (qty / span).quantize(_QTY_QUANTUM, rounding=ROUND_DOWN)
        tail = qty - base * (span - 1)
    return [_tidy(base)] * (span - 1) + [_tidy(tail)]


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


def _simulate_run(loads: list["_WeekLoad"], per_week: list[CapacityLimits],
                  open_weeks: list[int], code: str, qty: Decimal, start: int) -> Decimal:
    """How much of `qty` a CONTIGUOUS run beginning at open week `start`
    would actually place, given what is already booked.

    Mirrors the real fill loop exactly, including where it stops: the run
    ends at the first open week that can take nothing more of `code`,
    because hopping over that week would break P1."""
    placed = Decimal("0")
    remaining = qty
    for i in open_weeks[open_weeks.index(start):]:
        if remaining <= 0:
            break
        if not loads[i].sku_room(code, per_week[i]):
            break
        room = loads[i].remaining(per_week[i])
        take = remaining if room is None else min(remaining, room)
        if take <= 0:
            break
        placed += take
        remaining -= take
    return placed


def _run_start(loads: list["_WeekLoad"], per_week: list[CapacityLimits],
               open_weeks: list[int], code: str, qty: Decimal,
               prefer_fullest: bool) -> int | None:
    """Where a contiguous run of `code` should begin, or None if nowhere.

    `prefer_fullest=False` takes the earliest open week with any room at
    all. `prefer_fullest=True` takes the week whose run would PLACE THE MOST,
    ties going to the earliest -- P1 is untouched either way, the run is
    still one unbroken block, it just starts somewhere better. On
    `caps=[20, 5, 40, 30]` with `{A: 35, B: 40}` (B having taken W3), a run
    starting at W1 places 25 and stops at full W3; starting at W4 places 30.
    Same contiguity, 5 more product.

    **Read that example carefully: the run it describes only exists when
    `allow_last_resort_split=True`.** A(35) fits inside W3's 40 t, so
    `_fits_in_one_week` says yes and the non-splitting policy never reaches
    this function for it at all -- it declares a 35 t gap instead. Fullest
    start is INERT on its own: measured over 60,000 randomised buckets,
    `(split=False, fullest=True)` never once beat `(False, False)`, and over
    30,000 it produced zero layout differences. Its whole value is in
    combination with the last-resort split, i.e. the `(True, True)` corner.

    Neither policy is universally better once LATER items are considered,
    which is why `pack_bucket` evaluates all four combinations and keeps the
    plan that leaves the least demand unmet."""
    starts = []
    for i in open_weeks:
        if not loads[i].sku_room(code, per_week[i]):
            continue
        room = loads[i].remaining(per_week[i])
        if room is None or room > 0:
            starts.append(i)
    if not starts:
        return None
    if not prefer_fullest:
        return starts[0]
    # max() on (placed, -index): most placed wins, earliest breaks the tie.
    return max(starts, key=lambda i: (_simulate_run(loads, per_week, open_weeks,
                                                    code, qty, i), -i))


def _gap_reason(qty: Decimal, limits: CapacityLimits, all_closed: bool = False) -> str:
    if all_closed:
        # Pinning the line to a closed week and then reporting "under
        # max_output_qty 0" is technically true and completely unhelpful in
        # exactly the situation someone would be debugging.
        return (f"every week in this bucket is closed to production, "
                f"so {_tidy(qty)} cannot be placed here at all")
    ceilings = []
    if limits.max_sku_count is not None:
        ceilings.append(f"max_sku_count {limits.max_sku_count}")
    if limits.max_output_qty is not None:
        ceilings.append(f"max_output_qty {limits.max_output_qty}")
    ceiling_text = " and ".join(ceilings) or "no capacity"
    return f"no week left in this bucket for {_tidy(qty)} under {ceiling_text}"


def _pack_tight(ordered: list[BucketItem], weeks: list[date],
                per_week: list[CapacityLimits], open_weeks: list[int],
                allow_last_resort_split: bool = False,
                prefer_fullest_start: bool = False,
                preloaded: list[dict[str, Decimal]] | None = None,
                lot_of: "Callable[[str], Decimal | None] | None" = None) -> list[WeeklyLine]:
    """Tight regime: the bucket is over-full, so pack, do not spread.

    Two policy switches, because neither choice is universally right and
    `pack_bucket` resolves them by evaluating all four combinations (see its
    docstring, "Choosing between the packing policies"):

    - `allow_last_resort_split` -- may a product that fits no single open
      week be split anyway, rather than becoming a shortfall?
    - `prefer_fullest_start` -- should a run begin where it places the most,
      or at the earliest week with any room?

    `allow_last_resort_split=False, prefer_fullest_start=False` is the
    conservative baseline and is always one of the evaluated candidates, so
    the chosen plan can never leave more demand unmet than it would.

    The lot size never pushes a product into an EXTRA week here -- there is
    no slack to spread into, and doing so would manufacture a capacity gap
    out of nothing. What it does do is even out the tail: filling weeks to
    the brim leaves the remainder in the last one (90 t at a 40 t cap gives
    40+40+10), and a 10 t run costs the same changeover and cleandown as a
    30 t one. `_relevel_run` re-levels that run over the weeks it already
    occupies -- same weeks, same total, no gap -- so 90 t comes out
    30+30+30. When even levelling cannot reach the lot (weekly capacity is
    simply too small), the run is left levelled and flagged
    `below_min_lot`; capacity wins.

    Only weeks in `open_weeks` are placement targets; closed weeks (a
    maintenance shutdown, say) are stepped over, including in the middle of
    a split run. Contiguity is therefore contiguity **over open weeks**: a
    run crossing a shutdown costs no extra changeover, because the line is
    down anyway.
    """
    loads = [_WeekLoad() for _ in weeks]
    if preloaded:
        # Locked production is booked BEFORE anything is scheduled, exactly
        # as the month-based engine seeds its ledger. Because it lives in
        # the same `_WeekLoad` as everything else, a locked material is
        # already in that week's SKU set -- so the rest of the same product
        # joins its own locked week without being charged a second SKU slot,
        # and a run can flow straight through it instead of being cut in two.
        for index, held in enumerate(preloaded):
            for code, qty in held.items():
                loads[index].commit(code, qty)
    lines: list[WeeklyLine] = []

    for item in ordered:
        code, qty = item.material_code, item.qty
        spans_weeks = not _fits_in_one_week(qty, per_week, open_weeks)

        if spans_weeks:
            # Only a product that cannot physically fit in ANY single week
            # is allowed to be split, and then it starts at the earliest
            # week with ANY room, fills whole weeks, and drops its remainder
            # in the immediately following open week. Starting at the
            # earliest *empty* week instead would strand the partial week a
            # previous oversized product left behind, which in a bucket that
            # is by definition over-full turns spare capacity into a phantom
            # gap.
            start = _run_start(loads, per_week, open_weeks, code, qty,
                               prefer_fullest_start)
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
                (i for i in open_weeks
                 if loads[i].is_empty and loads[i].has_room_for(code, qty, per_week[i])),
                None,
            )
            if start is None:
                start = next(
                    (i for i in open_weeks
                     if loads[i].has_room_for(code, qty, per_week[i])),
                    None,
                )
            if start is None and allow_last_resort_split:
                # Last resort: NO single open week can hold the whole
                # quantity, so "never split what fits in one week" has
                # nothing left to protect -- its premise is false here.
                # Fall back to the greedy fill rather than declare a gap.
                #
                # This bites under heterogeneous per-week capacity, where
                # `_fits_in_one_week` says yes on the strength of the LARGEST
                # open week while placement needs ONE SPECIFIC week to have
                # the room. Once that big week is occupied the item could
                # neither split nor place, and became a phantom gap while
                # entirely empty (but smaller) weeks sat idle -- the exact
                # opposite of what per-week capacity was added to achieve.
                #
                # It is NOT unconditionally good, which is why it is a policy
                # rather than a rule: the sliver spilled into the next week
                # can poison it. Under `max_sku_count=1`, dropping 1 t into a
                # 40 t week destroys the other 39 t for every other product,
                # and the bucket ends up producing LESS in total than if this
                # item had simply been left short. `pack_bucket` therefore
                # also evaluates the plan without this split and keeps
                # whichever leaves less demand unmet.
                start = _run_start(loads, per_week, open_weeks, code, qty,
                                   prefer_fullest_start)

        remaining = qty
        last_used: int | None = None
        placed: list[tuple[int, int]] = []      # (index into `lines`, week index)
        if start is not None:
            for i in open_weeks[open_weeks.index(start):]:
                if remaining <= 0:
                    break
                load, limits = loads[i], per_week[i]
                if not load.sku_room(code, limits):
                    break              # stop rather than hop: P1 contiguity
                room = load.remaining(limits)
                take = remaining if room is None else min(remaining, room)
                if take <= 0:
                    break
                load.commit(code, take)
                placed.append((len(lines), i))
                lines.append(_line(item, weeks[i], take))
                remaining -= take
                last_used = i

        _relevel_run(lines, placed, loads, per_week, code,
                     lot_of(code) if lot_of else None)

        if remaining > 0:
            # Never silently drop demand: what did not fit surfaces as an
            # explicit gap line carrying the shortfall qty. Gap lines book
            # no capacity (they are a shortfall, not a production slot), and
            # are pinned to the last week this product actually occupied so
            # they stay inside its run -- or, when it got nowhere at all, to
            # the last OPEN week, never a closed one: a gap pinned to a
            # shutdown week reads as "no room under max_output_qty 0", which
            # is true and useless. Only when every week is closed is there
            # nowhere honest to pin it, and then the reason says so.
            if last_used is not None:
                gap_index = last_used
            elif open_weeks:
                gap_index = open_weeks[-1]
            else:
                gap_index = len(weeks) - 1
            lines.append(_line(item, weeks[gap_index], remaining,
                               capacity_gap=True,
                               prebuild_reason=_gap_reason(
                                   remaining, per_week[gap_index],
                                   all_closed=not open_weeks)))

    return lines


def _relevel_run(lines: list[WeeklyLine], placed: list[tuple[int, int]],
                 loads: list["_WeekLoad"], per_week: list[CapacityLimits],
                 code: str, lot: Decimal | None) -> None:
    """Even out one product's just-placed run so no week sits below `lot`.

    Same weeks, same total, so nothing is added to or taken from the bucket:
    a re-level can never turn met demand into a shortfall, and the
    shortfall-ranked choice between packing policies is unaffected.

    The span is never shortened. Dropping a week would mean removing the
    product from that week's SKU set, and `_WeekLoad` tracks a set of codes
    with one running total -- it cannot tell whether some OTHER item of the
    same material also occupies that week (two demand months of one product
    routinely land in one bucket). Levelling in place needs no such
    accounting.

    Leaves the run untouched when levelling would breach a week's own
    ceiling, which heterogeneous per-week capacity allows: an uneven run
    that fits beats an even one that does not.
    """
    if lot is None or len(placed) < 2:
        return
    quantities = [lines[li].qty for li, _ in placed]
    if all(q >= lot for q in quantities):
        return

    total = sum(quantities, Decimal("0"))
    chunks = _level(total, len(placed))
    for pos, (_, week_index) in enumerate(placed):
        ceiling = per_week[week_index].max_output_qty
        if ceiling is None:
            continue
        others = loads[week_index].qty - quantities[pos]
        if others + chunks[pos] > ceiling:
            return

    for pos, (line_index, week_index) in enumerate(placed):
        loads[week_index].qty += chunks[pos] - quantities[pos]
        lines[line_index] = replace(lines[line_index], qty=_tidy(chunks[pos]))


def _pack_spare(ordered: list[BucketItem], weeks: list[date],
                per_week: list[CapacityLimits], open_weeks: list[int],
                needs: list[int],
                lot_of: "Callable[[str], Decimal | None]") -> list[WeeklyLine]:
    """Spare regime: more open weeks than the demand strictly needs.

    Every product starts at its physical minimum `need_weeks` and may grow
    up to `_spread_ceiling`; the leftover weeks go one at a time to whichever
    product currently carries the heaviest per-week load (P2: spread the work
    out instead of cramming the front of the month and idling the back).
    Growth stops at the `min_output_qty` floor, and **the weeks nobody can
    use stay empty** -- splitting 20 t into four 5 t weeks burns energy for
    nothing. Products are then laid out as contiguous blocks, largest first,
    across the OPEN weeks in order (closed weeks are simply not slots).

    No week can be overfilled here: `_reference_cap` sizes `need_weeks` off
    the smallest open week's cap, so `qty / span <= that cap <= every open
    week's cap`. And every week holds exactly one product, so `max_sku_count`
    cannot be breached either (a ceiling below 1 closes the week outright,
    and `pack_bucket` routes a bucket with no open weeks at all to
    `_pack_tight`).
    """
    slot_count = len(open_weeks)
    # Keyed by POSITION in `ordered`, never by `id(item)`: `BucketItem` is a
    # frozen dataclass, so a caller passing the same object twice would have
    # the two entries collide and the layout loop could then run off the end
    # of `open_weeks`.
    assigned = list(needs)
    # Per PRODUCT, not per factory: the floor is "what is worth opening the
    # line for", and that differs by product.
    ceilings = [_spread_ceiling(item.qty, needs[pos], lot_of(item.material_code), slot_count)
                for pos, item in enumerate(ordered)]

    spare = slot_count - sum(assigned)
    while spare > 0:
        candidates = [pos for pos in range(len(ordered)) if assigned[pos] < ceilings[pos]]
        if not candidates:
            break                      # everyone is at their floor: leave weeks empty
        # Heaviest per-week load first; ties by larger total qty, then by
        # material_code / demand_month for a fully deterministic answer.
        # Recomputed every round, so a product that has just been widened
        # drops down the ranking and the next week flows elsewhere.
        heaviest = min(
            candidates,
            key=lambda pos: (-(ordered[pos].qty / Decimal(assigned[pos])),
                             -ordered[pos].qty,
                             ordered[pos].material_code,
                             ordered[pos].demand_month),
        )
        assigned[heaviest] += 1
        spare -= 1

    lines: list[WeeklyLine] = []
    cursor = 0
    for pos, item in enumerate(ordered):
        span = assigned[pos]
        for offset, chunk in enumerate(_level(item.qty, span)):
            lines.append(_line(item, weeks[open_weeks[cursor + offset]], chunk))
        cursor += span
    return lines


def _normalize_limits(limits: "CapacityLimits | Sequence[CapacityLimits]",
                      week_count: int) -> list[CapacityLimits]:
    """One `CapacityLimits` applies to every week; a sequence binds
    positionally and must be exactly as long as `weeks`.

    A length mismatch is a hard `ValueError`: silently zipping to the
    shorter of the two would quietly plan a maintenance shutdown into the
    wrong week, which is worse than not planning at all."""
    if isinstance(limits, CapacityLimits):
        return [limits] * week_count
    per_week = list(limits)
    if len(per_week) != week_count:
        raise ValueError(
            f"pack_bucket got {len(per_week)} CapacityLimits for {week_count} weeks; "
            "a per-week sequence must line up with `weeks` exactly."
        )
    return per_week


def _normalize_preload(preloaded: "Sequence[dict[str, Decimal]] | None",
                       week_count: int) -> list[dict[str, Decimal]]:
    """Per-week already-committed load, positionally bound to `weeks`.

    Same contract as `_normalize_limits`: a length mismatch is a hard
    `ValueError`, because booking a locked batch into the wrong week is
    worse than refusing to plan at all."""
    if preloaded is None:
        return [{} for _ in range(week_count)]
    rows = [dict(row) for row in preloaded]
    if len(rows) != week_count:
        raise ValueError(
            f"pack_bucket got {len(rows)} preloaded weeks for {week_count} weeks; "
            "a per-week preload must line up with `weeks` exactly.")
    return rows


def pack_bucket(items: list[BucketItem], weeks: list[date],
                limits: "CapacityLimits | Sequence[CapacityLimits]",
                preloaded: "Sequence[dict[str, Decimal]] | None" = None,
                min_lots: "dict[str, Decimal] | None" = None) -> list[WeeklyLine]:
    """Pack one bucket's net requirements into that bucket's weeks.

    Pure function. `weeks` is an already-resolved ascending list of week-start
    dates (see `week_calendar.weeks_of_month`); this function never branches
    on which week mode produced it. `limits` is already resolved by the
    caller (see `capacity.resolve_limits_for_week`); this function never
    touches the DB.

    `limits` is either one `CapacityLimits` applied to every week, or a
    sequence of exactly `len(weeks)` of them binding positionally --
    matching what `resolve_limits_for_week(db, week_start)` actually
    produces, which is per week. **A week whose `max_output_qty` is 0 (or
    whose `max_sku_count` is below 1) is closed** -- a maintenance shutdown,
    typically. It is skipped as a placement target and its production goes
    to other weeks; it does NOT turn the bucket into a shortfall.

    ## The three principles it enforces

    - **P1 -- a product's run is contiguous.** Changeovers cost a cleandown
      each way, so A-then-B-then-A is never planned. A product whose quantity
      fits within some week's capacity is never split *while a week that can
      still take it whole exists*; only a product exceeding every week's
      capacity may span weeks, and then it fills whole weeks and drops its
      remainder in the immediately following open week. As a LAST RESORT --
      when no open week, empty or otherwise, can take the whole quantity --
      even a "small" product is split rather than turned into a shortfall
      while capacity sits idle. Contiguity is measured over OPEN weeks: a run
      stepping over a shutdown week costs no changeover, because the line is
      down anyway.
    - **P2 -- when the month is not full, spread out** rather than cramming
      the first weeks and idling the plant at the end.
    - **P3 -- prefer one product per week.** Fewer changeovers again; a
      product only shares a week when the calendar has no empty week left.

    ## The two regimes

    `need_weeks(p) = ceil(qty / ref_cap)`, where `ref_cap` is the smallest
    open week's `max_output_qty` (1 week when unlimited) -- see
    `_reference_cap` for why the smallest and not the largest.

    - **Tight** (`sum(need_weeks) > len(open weeks)`) -- see `_pack_tight`.
      `min_output_qty` does not apply: there is no room to thin anything.
    - **Spare** (`sum(need_weeks) <= len(open weeks)`) -- see `_pack_spare`.
      Empty weeks are a legitimate result.

    The predicate is strictly greater. `need_weeks` is a CEILING, so its
    slack is not consumed capacity, and treating equality as tight sends
    comfortable months down the packing path: `{A: 60, B: 60}` at cap 40
    over 4 weeks has `sum(need_weeks) == 4` yet only needs 120 of the 160
    available. Tight would produce W1 40, W2 A20+B20, W3 40 and an idle W4 --
    a P2 violation manufactured entirely by the ceiling's rounding. Spare
    levels it to 30/30/30/30, which is what the plant manager would draw.

    ## What a `capacity_gap` line means at this layer

    **"Did not fit in THIS bucket" -- NOT the design's final shortfall.**
    Per design §2.0 step ⑤, quantity that will not fit is supposed to
    overflow BACKWARDS into earlier weeks (pre-build) before any of it is a
    real gap; only running into the current week or the shelf-life limit
    makes a shortfall final. `pack_bucket` sees exactly one bucket and
    cannot do that search, so its gap lines are provisional: they are the
    input to the cross-bucket pre-build step, not its output.

    **A later task must resolve them before persisting.** Storing them
    as-is would show a planner a permanent shortfall for demand that could
    have been pre-built a week or two earlier.

    ## Edge cases and tie-breaks (none of which the design pinned down)

    - **Equal quantities** order by `material_code` ascending, then
      `demand_month` -- arbitrary but stable, so the same input always
      produces the same plan.
    - **A gap line is never pinned to a closed week** unless every week in
      the bucket is closed, in which case the reason says exactly that
      instead of blaming the closed week's `max_output_qty 0`.
    - **A run stops at the first open week that cannot take any more of it**
      (already full, or too small) rather than hopping over it, because
      hopping would break P1. The lever that remains is WHERE the run
      begins, and it begins where it places the most (`_run_start`): on
      `caps=[20, 5, 40, 30]` with `{A: 35, B: 40}`, B takes W3 and A then
      runs from W4 alone, placing 30 and falling 5 short -- rather than
      running W1(20) + W2(5), stopping dead at full W3, and falling 10
      short. (That run is itself a last-resort split; the start choice
      does nothing without one -- see `_run_start`.) **Open weeks left
      idle beside a gap are therefore a deliberate outcome, not a bug**:
      the plan that occupies W1 and W2 makes less product. Unmet demand
      is the objective; week occupancy is not. About 85 of 30,000
      randomised heterogeneous buckets end this way. Uniform
      capacity CANNOT: measured 0 of 30,000, because there an empty week is
      a full-capacity week, so the "whole quantity" search would have found
      it before any greedy fill ran.
    - **A product larger than the whole bucket's capacity** fills every week
      it can reach; the unproducible remainder becomes one `capacity_gap`
      line pinned to the last week it occupied. That week therefore carries
      two lines for that material (one real, one gap). The gap books no
      capacity and is not part of the product's physical run -- so a
      per-week SKU count or a contiguity check that counts gap lines will
      misread it. Filter `capacity_gap` out before either.
    - **`qty <= 0` items are dropped** -- a zero net requirement is nothing
      to produce, and a negative one is upstream nonsense that must not be
      turned into a negative production line (nor divided by, when computing
      `need_weeks`). Nothing is lost: there was nothing to lose.
    - **A bucket with no weeks raises `ValueError`** when there is anything
      to pack. Returning `[]` would silently drop real demand, which this
      module never does. An empty `items` list with no weeks returns `[]`.
    - **A bucket whose every week is closed** routes to `_pack_tight`, where
      every item becomes an explicit gap -- including in what would
      otherwise be the spare regime, which lays out one product per week and
      so normally never consults the SKU ceiling at all.
    - **`max_sku_count` blocking the leftover-packing step** (a small product
      that would fit a week's spare qty, but that week already holds the
      maximum number of SKUs) makes that week ineligible; if no week is
      eligible anywhere, the product becomes a `capacity_gap` rather than
      breaching the ceiling.
    ## Pre-seeded weeks (locked production)

    `preloaded` is an optional per-week `{material_code: qty}` map, bound
    positionally to `weeks`, describing production a planner has already
    locked. It is booked into each week's ledger before scheduling starts,
    so locked material participates in SKU accounting and contiguity
    naturally rather than being simulated by shrinking `max_output_qty` and
    `max_sku_count` from outside -- which would charge a second SKU slot to
    a product joining its OWN locked week, and would leave a run unable to
    abut that week, splitting one product into two runs.

    **A bucket with any preload is packed by `_pack_tight`, never
    `_pack_spare`.** The spare regime lays contiguous blocks over weeks it
    assumes are empty and takes exactly one product per week; both
    assumptions are false the moment a week is pre-committed, and it
    consults no ledger at all (the same reason an all-closed bucket is
    routed to `_pack_tight`). The cost is that levelling is suppressed in a
    bucket that contains locked production -- a known, accepted trade, not
    an oversight. Making `_pack_spare` load-aware is the cheaper long-term
    fix and was deliberately deferred rather than attempted in a bucket
    whose internals four review rounds have already pinned.

    - **Two BucketItems with the same `material_code`** (which Task 5's
      bucketing by lead-shifted target month produces routinely, when two
      demand months land in one bucket) are packed as two independent runs.
      They count as ONE SKU when they share a week, which is physically
      right; but P1 contiguity is then guaranteed per BucketItem, not per
      material. Task 5 must decide explicitly whether to merge them first.
    """
    if not weeks:
        if not items:
            return []
        raise ValueError(
            "pack_bucket was given demand but no weeks to place it in; "
            "refusing to silently drop it."
        )

    per_week = _normalize_limits(limits, len(weeks))
    held = _normalize_preload(preloaded, len(weeks))

    payload = [item for item in items if item.qty > 0]
    if not payload:
        return []

    ordered = sorted(payload, key=_sort_key)
    open_weeks = [i for i, wk in enumerate(per_week) if _week_can_host(wk)]

    # A product's own minimum lot size, falling back to the factory-wide
    # floor. The factory floor is bucket-wide -- it says how thinly the plant
    # is willing to run at all, which is not a property of an individual
    # week -- so the LARGEST configured floor across the open weeks is taken,
    # and no week is ever asked to run below its own minimum.
    factory_floors = [per_week[i].min_output_qty for i in open_weeks
                      if per_week[i].min_output_qty is not None]
    factory_floor = max(factory_floors) if factory_floors else None

    def lot_of(code: str) -> Decimal | None:
        if min_lots is not None and code in min_lots:
            return min_lots[code]
        return factory_floor

    if not open_weeks:
        # Nothing can be produced anywhere in this bucket. `_pack_tight`'s
        # per-week checks turn every item into an explicit gap; `_pack_spare`
        # has no slots to lay anything out in and would silently drop them.
        return _sorted_lines(_pack_tight(ordered, weeks, per_week, open_weeks,
                                         preloaded=held, lot_of=lot_of))

    ref_cap = _reference_cap(per_week, open_weeks)
    needs = [_need_weeks(item.qty, ref_cap) for item in ordered]

    if any(held) or sum(needs) > len(open_weeks):
        # Both packing policies are genuinely ambiguous (see `_pack_tight`),
        # and either can be the wrong call depending on items this one has
        # not seen yet. So evaluate all four combinations and keep the plan
        # that leaves the least demand unmet -- the single ranking key, see
        # `_plan_shortfall`. The conservative baseline `(False, False)` is
        # always among them and is generated first, and `min()` returns the
        # FIRST minimum, so it also wins every tie: a split has to EARN its
        # changeover with product, not merely match the conservative plan.
        plans = [
            _pack_tight(ordered, weeks, per_week, open_weeks,
                        allow_last_resort_split=split,
                        prefer_fullest_start=fullest, preloaded=held,
                        lot_of=lot_of)
            for split in (False, True)
            for fullest in (False, True)
        ]
        lines = min(plans, key=_plan_shortfall)
    else:
        lines = _pack_spare(ordered, weeks, per_week, open_weeks, needs, lot_of)

    return _sorted_lines(_flag_below_min_lot(lines, lot_of))


def _flag_below_min_lot(lines: list[WeeklyLine],
                        lot_of: "Callable[[str], Decimal | None]") -> list[WeeklyLine]:
    """Mark every produced week that ends up under its product's lot size.

    Applied once, at the end, over BOTH regimes: the tight path can be left
    below the floor by a re-level that capacity refused, and the spare path
    by `_spread_ceiling` (where `need_weeks` -- a physical minimum -- always
    outranks the floor). One pass means the flag cannot disagree with the
    quantities beside it.

    Gap lines are never flagged: a shortfall is not a production run."""
    flagged: list[WeeklyLine] = []
    for line in lines:
        lot = lot_of(line.material_code)
        if not line.capacity_gap and line.qty > 0 and lot is not None and line.qty < lot:
            flagged.append(replace(line, below_min_lot=True))
        else:
            flagged.append(line)
    return flagged


def _plan_shortfall(lines: list[WeeklyLine]) -> Decimal:
    """How bad a candidate plan is: how much demand it leaves unmet.

    Total demand in a bucket is fixed, so less shortfall is strictly more
    product made. That is the ONLY ranking key: `pack_bucket` evaluates the
    conservative plan first and `min()` keeps the first minimum, so an equal
    score leaves the conservative plan in place and a split has to EARN its
    changeover with product it actually gains.

    There is deliberately no secondary key. Counting non-gap lines was tried
    and removed: it does not measure changeovers (one run spanning three
    weeks is three lines but a single cleandown), and it let a split win a
    tie having bought nothing. On three uniform 50 t weeks with
    `{A:1, B:10, C:46, D:8, E:103, F:48}` and `max_sku_count=4`, both plans
    leave 66 unmet -- but the conservative one produces C(46) and A(1)
    whole, while the split fragments F into a 47 t partial batch and leaves
    C, A, B and D entirely short. A planner can ship a whole C; nobody can
    ship 47/48ths of an F. Equal shortfall, so keep the products whole."""
    return sum((l.qty for l in lines if l.capacity_gap), Decimal("0"))


def _sorted_lines(lines: list[WeeklyLine]) -> list[WeeklyLine]:
    return sorted(lines, key=lambda l: (l.plan_week_start, l.material_code,
                                        l.demand_month, l.capacity_gap))


# ══════════════════════════════════════════════════════════════════════════
# Weekly pipeline (weekly-MPS Task 5)
# ══════════════════════════════════════════════════════════════════════════


def _month_first_day(month: str) -> date:
    """First calendar day of a `'YYYY-MM'` month."""
    return date(int(month[:4]), int(month[5:7]), 1)


def _minus_months(anchor: date, months: int) -> date:
    """`anchor` moved back `months` whole calendar months, with the day of
    month clamped to the target month's length (31 Mar - 1 month = 28 or 29
    Feb, never 2 or 3 Mar).

    Deliberately NOT `anchor - timedelta(days=30 * months)`, and not
    `4.33 * 7` days per month either: design §2.6 forbids approximating
    months because the shelf-life gate below works at week resolution.

    **The measured reason, which is not the one the design first gave.**
    `4.33 weeks/month` does NOT drift by a whole week over 18 months --
    measured across every anchor month in 2024-2028 for n = 1..18 the worst
    deviation is **4.42 days** (the cruder `30 days/month` does reach 10).
    The ban stands on something better: **a deviation never has to reach a
    whole week to cross a week boundary.** There are real ISO Mondays the
    approximation admits and the real calendar refuses, clustered at
    n = 1..3 because February is short. See `_prebuild_allowed` for a
    worked one.

    **Do not quote a count without the enumeration that produced it** -- it
    is not a property of the calendar, it is a property of how many
    anchors, horizons and margins you sweep. Same script, different sweeps:
    8 over 2025-2027 at margin 1/3 alone; 20 over 2025-2027 across margins
    {0, 0.1, 1/3, 0.5}; 22 over 2024-2028 across the same four. Spec §2.6
    dropped its count for this reason (`18cd61c`).
    """
    total = anchor.year * 12 + (anchor.month - 1) - months
    year, month0 = divmod(total, 12)
    last_day = monthrange(year, month0 + 1)[1]
    return date(year, month0 + 1, min(anchor.day, last_day))


def _prebuild_allowed(plan_week_start: date, demand_month: str,
                      shelf_life_months: int | None,
                      safety_margin_fraction: Decimal) -> bool:
    """Real calendar-day comparison, deliberately not 4.33 weeks/month.

    An off-by-one week on a shelf-life gate is the difference between
    shippable stock and a write-off. A worked divergence, measured rather
    than argued: three months of shelf life ending at 2026-05-01 is 89 real
    days (February is short), but 3 x 4.33 x 7 = 90.93 days. At a 1/3 safety
    margin that is 59 allowed days against the approximation's 60 -- and the
    ISO week starting 2026-03-02 sits exactly 60 days before that demand
    month, so the approximation would plan a whole week of production that
    the real calendar says expires before it ships. That week is not the
    only one; how many others there are depends entirely on how wide a
    sweep you run, so `_minus_months` records the enumeration beside every
    number rather than quoting a bare count.

    **Two roundings, and the order matters.** The counts above apply the
    margin to the UNFLOORED approximate horizon
    (`int(n * Decimal("4.33") * 7 * (1 - margin))`). Flooring the horizon
    first (`int(n * 4.33 * 7)`, then the margin) halves them -- 4 against 8,
    10 against 20, 11 against 22 -- because the two roundings sometimes
    cancel. Which is the point: these numbers describe one specific wrong
    formula, not "approximation" in the abstract.

    **What the design first claimed, and what is actually true**: it said
    the approximation drifts by whole weeks over an 18-month horizon. For
    `4.33 weeks/month` it does not -- worst measured deviation is 4.42 days.
    The ban is still right; the reason is that 4.42 days is more than enough
    to cross a week boundary, not that it accumulates to seven.

    **The reference point is the demand month's FIRST day**, not its last,
    which keeps this consistent with the month-based engine's
    `prebuild_months <= floor(shelf_life * (1 - margin))`.

    Unknown shelf life (`None`) can never be pre-built: fail safe. Callers
    must therefore only consult this for weeks genuinely EARLIER than the
    lead-shifted target week -- placing at (or after) the target is not a
    pre-build and is never gated, exactly as the month-based engine treats
    an offset of zero as always legal regardless of shelf life.
    """
    if shelf_life_months is None:
        return False
    demand_start = _month_first_day(demand_month)
    horizon_days = (demand_start - _minus_months(demand_start, shelf_life_months)).days
    allowed_days = int(Decimal(horizon_days) * (Decimal("1") - safety_margin_fraction))
    return (demand_start - plan_week_start).days <= allowed_days


def _placement_allowed(week: date, demand_month: str, shelf_life_months: int | None,
                       safety_margin_fraction: Decimal, weeks_early: int) -> bool:
    """May this material be produced in this week for this demand month?

    ONE rule, applied at every placement point -- the bucket's own weeks and
    every step of the backward walk alike. Having two nearly-identical rules
    is how the same false-shortfall bug got written twice.

    - **Known shelf life** -> the real-date gate, on EVERY week, including
      the lead-shifted target itself and weeks later than it (which pass
      trivially, being closer to the demand month). Gating the target is the
      weekly form of the month engine's "the lead alone already demands more
      pre-build than shelf life allows".
    - **Unknown shelf life** -> never earlier than the target, and no
      calendar check at all. The lead is a configured plant parameter, not a
      discretionary decision; refusing to plan anything for a product whose
      ERP shelf-life field is blank would return an empty plan instead of a
      visible one. Every DISCRETIONARY step is still refused: no pre-build,
      ever, not one week.

      **This carries an obligation the engine cannot discharge alone.** A
      product with no shelf life on record silently loses all of its
      pre-build headroom, and from the outside that is indistinguishable
      from ordinary capacity pressure. `app/api/v1/mps.py` discharges it:
      every run's `stats["no_shelf_life"]` names the products it planned
      with no shelf life on record (design §7). If that ever stops being
      reported, this branch goes back to being an invisible degradation.

    Monotone in the week under both branches (an earlier week is never more
    legal than a later one), which is what lets the backward walk stop at
    its first refusal instead of searching on.
    """
    if shelf_life_months is None:
        return weeks_early == 0
    return _prebuild_allowed(week, demand_month, shelf_life_months,
                             safety_margin_fraction)


def _peel(queue: list[list], amount: Decimal) -> list[tuple[str, Decimal]]:
    """Take `amount` off the front of `queue` (`[demand_month, qty]` pairs
    held in ASCENDING demand-month order), returning the slices consumed.

    This is the whole of the "attribute a produced week back to demand
    months" rule: the earliest demand month is satisfied first, so any
    shortfall lands on the LATEST demand month. Deliberately not pro rata --
    a planner who reads "October is short 50" acts differently from one who
    reads "everything is 8% short"."""
    taken: list[tuple[str, Decimal]] = []
    while amount > 0 and queue:
        demand_month, available = queue[0]
        take = amount if amount < available else available
        taken.append((demand_month, take))
        amount -= take
        if take == available:
            queue.pop(0)
        else:
            queue[0][1] = available - take
    return taken


def _early_note(weeks_early: int, bucket_reason: str | None) -> str:
    """Reason text for a line that crossed into an earlier bucket month.

    Only ever reached for a genuine cross-bucket pre-build; levelling inside
    the demand's own bucket carries no reason at all, because nothing
    happened that needs explaining."""
    plural = "" if weeks_early == 1 else "s"
    note = f"pre-built {weeks_early} week{plural} early"
    return f"{note}: {bucket_reason}" if bucket_reason else note


def _merge_same_slot(lines: list[WeeklyLine]) -> list[WeeklyLine]:
    """Fold two lines describing the same slot into one.

    The backward walk can land in a week that already holds the same
    material for the same demand month (the packer left room there -- e.g.
    `max_sku_count` blocked everyone else from using it). Two rows for one
    material in one week read as two production runs; physically it is one.
    Only identical slots merge: a real line and a `capacity_gap` line are
    never folded together, because that would hide a shortfall inside a
    production quantity."""
    folded: dict[tuple, int] = {}
    out: list[WeeklyLine] = []
    for line in lines:
        key = (line.material_code, line.demand_month, line.plan_week_start,
               line.capacity_gap, line.is_prebuild, line.weeks_early,
               line.shelf_life_ok, line.locked, line.lead_shortfall)
        at = folded.get(key)
        if at is None:
            folded[key] = len(out)
            out.append(line)
        else:
            seen = out[at]
            out[at] = WeeklyLine(
                material_code=seen.material_code, demand_month=seen.demand_month,
                plan_week_start=seen.plan_week_start,
                plan_week_month=seen.plan_week_month,
                qty=_tidy(seen.qty + line.qty),
                is_prebuild=seen.is_prebuild, weeks_early=seen.weeks_early,
                prebuild_reason=seen.prebuild_reason or line.prebuild_reason,
                shelf_life_ok=seen.shelf_life_ok, capacity_gap=seen.capacity_gap,
                locked=seen.locked, lead_shortfall=seen.lead_shortfall)
    return out


def generate_mps(
    demands: list[DemandItem],
    limits_for_week: Callable[[date], CapacityLimits],
    shelf_life_months: dict[str, int | None],
    safety_margin_fraction: Decimal,
    lead_weeks: int,
    current_week: date,
    mode: str,
    start_dow: int,
    locked: list[WeeklyLine] | None = None,
    min_lots: dict[str, Decimal] | None = None,
) -> list[WeeklyLine]:
    """The weekly master production schedule, per design §2.0's six steps.

    Pure function: no DB, no clock. `limits_for_week` is the caller's
    already-bound resolver (Task 3's `resolve_limits_for_week` partial);
    `current_week` is the "now" this run schedules from and is normalised to
    its own week start, so a caller may pass any day of the current week.
    `start_dow` says which weekday a week begins on (0=Monday .. 6=Sunday);
    it has no default on purpose -- callers must pass the RUN's stored
    `week_start_dow`, never the current planning parameter, or a released
    plan would silently re-bucket itself onto a different grid.

    ## The pipeline

    1. **Net requirements** arrive as `DemandItem`s (material x demand
       month), unchanged from the month-based engine.
    2. **Lead shift.** `target_week = last week of the demand month -
       lead_weeks`, clamped never to fall before `current_week`; the clamp
       sets `lead_shortfall` on every line of that demand month.
    3. **Bucket** by the OWNING MONTH of the target week -- `week_calendar`
       decides ownership; this module never branches on `mode` itself.
    4. **Pack** each bucket with `pack_bucket`.
    5. **Overflow backwards**, week by week, across bucket boundaries if
       need be, every step gated by `_prebuild_allowed`.
    6. **Hitting the current week, or failing shelf life, is a
       `capacity_gap`** -- demand is never silently dropped.

    ## The bucket's canvas is the whole month, and the gate covers all of it

    A bucket's weeks are its owning month's full 4/5 weeks (minus anything
    before `current_week`), per design §2.0 ④. **Every placed week earlier
    than its own target week passes `_prebuild_allowed`** -- not just the
    step-5 overflow. The two halves are inseparable: the design originally
    stated the gate only on the overflow step, which would have let a
    whole-month canvas place production weeks ahead of target with no
    shelf-life check at all (a write-off waiting to happen), and spec
    `dc9a78e` fixed the omission rather than shrinking the canvas.

    Shrinking the canvas to start at the bucket's earliest target week was
    tried and rejected, with numbers: at the default `lead_weeks=4` four of
    the eight active buckets over 2026-06..2027-06 came out with a
    ONE-WEEK canvas (a 4-week bucket month receives exactly one demand
    month, whose target is that month's last week), where P2 levelling and
    P3 one-product-per-week are structurally inoperative. (That measurement
    also flagged 39% of a gap-free plan's lines as `is_prebuild`, 62% at
    `lead_weeks=0`. Widening the canvas alone did not fix that -- it made it
    worse, 58% and 87% -- because the target week sits at one END of the
    bucket, so every levelled line is "earlier than target". The flag's
    definition was the actual defect; see below.)

    `lead_weeks=0` therefore means production lands **inside the demand
    month**, not "in its last week only": the month-based engine whose
    no-shift behaviour it reproduces places by month.

    ## `is_prebuild` is a bucket question; `weeks_early` is a week distance

    They deliberately do not track each other -- see `WeeklyLine`'s field
    comments. `is_prebuild` is true only when a line's owning month precedes
    the demand's own bucket month, i.e. production was genuinely pulled into
    an earlier month. Anything inside the bucket, however early in it, is
    levelling. `weeks_early` remains the plain week distance from the target
    because that is the quantity the shelf-life gate reasons about, so a
    levelled line normally reads `weeks_early > 0, is_prebuild=False`.

    ## Same material, two demand months, one bucket: merged before packing

    Lead-shifting routinely lands two demand months of one material in one
    bucket, and `pack_bucket` guarantees contiguity per `BucketItem`, NOT
    per material -- two non-contiguous runs of one product is precisely the
    changeover cost P1 exists to prevent. So demand months are merged per
    material before packing, and each produced week is attributed back
    afterwards, earliest demand month first (see `_peel`). That attribution
    order is also the shelf-life-optimal one: the earliest demand month has
    the earliest reference date and therefore the least room to be
    pre-built, and it is exactly the one handed the earliest weeks.

    A merged item may therefore be placed earlier than the target of the
    LATER demand month it partly serves. Such a slice is gated like any
    other pre-build, and one that fails **rejoins the backward walk** (it is
    NOT emitted as a shortfall on the spot). That matters: the walk starts
    at the bucket's last week, so the slice is re-offered every week the
    bucket has left -- **including its own target week, where it is not
    early and the gate does not apply at all**. Emitting the gap directly
    reported a shortfall against an empty target week, which a planner would
    escalate or expedite against.

    ## Minimum lot sizes

    `min_lots` maps material code to the quantity below which opening the
    line is not worth the changeover. A bucket whose whole requirement for a
    product falls under that lot is **rounded up to it** -- the plant would
    rather make a full batch now and draw on it later than run the line for
    a token quantity. The excess is recorded as `surplus_qty`.

    A rounded batch is placed **whole, in one week**: splitting it puts both
    halves back under the lot, which is the thing being avoided. If no week
    of its own bucket can host it, the batch is not produced there -- it goes
    to the same backward walk pre-builds use, and if that fails, forward
    (`late_production`). Only when the entire horizon has no room for it does
    it become a `capacity_gap`.

    Products with no entry fall back to the factory-wide `min_output_qty` of
    their bucket, and a product with neither has no floor at all.

    ## Locked lines

    `locked` is production a planner has already committed. Each line is
    echoed into the result byte for byte, booked into its week before
    anything is scheduled (via `pack_bucket`'s `preloaded`, and into the
    backward walk's ledger), and its quantity is subtracted from the
    matching `(material_code, demand_month)` demand so only the remainder is
    re-planned. Locked quantity beyond what is still demanded is echoed and
    still consumes capacity -- a planner may have locked more than the
    current forecast asks for, and this engine does not overrule that.

    Subtracting by week rather than dropping the whole `(material_code,
    demand_month)` demand is the weekly-specific part: a month's demand can
    be part locked and part open, and dropping the key entirely would
    silently delete the open remainder.
    """
    current = week_start_of(current_week, mode, start_dow=start_dow)

    # A locked line carrying `capacity_gap=True` is DROPPED, not echoed.
    # Callers do produce them: the API layer rebuilds locked lines from
    # whatever the planner locked, `capacity_gap` flag and all.
    #
    # A shortfall is not committed production, so there is nothing to
    # preserve -- and echoing one while the same demand is re-planned in
    # full double-counts it (100 t of demand plus a stale 40 t gap line came
    # out as a 140 t plan, breaking the conservation invariant this module
    # raises `RuntimeError` elsewhere to protect).
    #
    # Subtracting it from demand instead would be far worse than either:
    # that turns "we could not make this" into "we no longer need this" and
    # deletes real demand permanently, hiding the shortage rather than
    # re-reporting it. A gap is DERIVED data -- this run recomputes it from
    # current demand and current capacity, which is the entire point of
    # recalculating -- so the only correct thing to do with a stale one is
    # to discard it and let the shortage prove itself again.
    locked_lines = [line for line in (locked or []) if not line.capacity_gap]
    ledger: dict[date, _WeekLoad] = {}
    locked_by_week: dict[date, dict[str, Decimal]] = {}
    held_by_demand: dict[tuple[str, str], Decimal] = {}
    for line in locked_lines:
        key = (line.material_code, line.demand_month)
        held_by_demand[key] = held_by_demand.get(key, Decimal("0")) + line.qty
        week = locked_by_week.setdefault(line.plan_week_start, {})
        week[line.material_code] = week.get(line.material_code, Decimal("0")) + line.qty
        ledger.setdefault(line.plan_week_start, _WeekLoad()).commit(
            line.material_code, line.qty)

    # `pack_bucket` drops non-positive quantities (a zero net requirement is
    # nothing to produce, a negative one is upstream nonsense that must not
    # become a negative production line); drop them here too, so they never
    # reach the bucketing arithmetic either. Locked quantity comes off the
    # matching demand BY WEEK, leaving the open remainder to be re-planned --
    # dropping the whole (material, demand month) key would delete it.
    payload: list[DemandItem] = []
    for item in demands:
        if item.qty <= 0:
            continue
        key = (item.material_code, item.demand_month)
        already = held_by_demand.get(key, Decimal("0"))
        covered = item.qty if item.qty < already else already
        if covered > 0:
            held_by_demand[key] = already - covered
        left = item.qty - covered
        if left > 0:
            payload.append(DemandItem(item.material_code, item.demand_month, left))
    if not payload:
        # Every demand is already covered by locked lines, so there is
        # nothing to pack -- but this early return still owes the caller the
        # SAME shape the long path produces at the bottom of this function,
        # `_merge_same_slot` included. It is not an optimisation detail:
        # AdjustDrawer's "Merge into adjacent week" locks BOTH lines onto one
        # slot precisely so that the next recalculate folds them into a
        # single row, and a single-product plan whose demand the two locked
        # lines fully cover takes exactly this path. Returning unmerged here
        # made the merge silently do nothing whenever it was the only thing
        # the run had to do, and quietly work as soon as any unrelated open
        # demand existed.
        return _sorted_lines(_merge_same_slot(locked_lines))

    # ② Lead shift, per demand month -- every item of a demand month shares
    #    one target week, because the target depends only on the month.
    targets: dict[str, date] = {}
    clamped: dict[str, bool] = {}
    for month in {d.demand_month for d in payload}:
        standard = shift_weeks(weeks_of_month(month, mode, start_dow=start_dow)[-1],
                               -lead_weeks, mode, start_dow=start_dow)
        clamped[month] = standard < current
        targets[month] = current if clamped[month] else standard

    lines: list[WeeklyLine] = list(locked_lines)

    # NOTE: there is deliberately no "the lead itself outruns shelf life"
    # pre-check here any more. It used to emit a shortfall on the spot, which
    # is the SAME false-shortfall bug as the in-bucket one below: a target
    # week that shelf life refuses does not mean the bucket has no legal week
    # -- weeks LATER than the target are closer to the demand month and pass
    # trivially. Producing late is worse than producing on the lead, and far
    # better than not producing at all. `_placement_allowed` now guards every
    # placement uniformly and anything it refuses rejoins the backward walk,
    # so a legal later week is found if one exists and only a genuinely
    # unplaceable quantity becomes a gap.

    # ③ Bucket by the owning month of the target week.
    buckets: dict[str, list[DemandItem]] = {}
    for item in payload:
        buckets.setdefault(
            owning_month(targets[item.demand_month], mode, start_dow=start_dow),
            []).append(item)

    # The last week production may be scheduled into at all: the final week
    # of the latest bucket month. The forward walk (below) stops here rather
    # than running off into weeks no capacity was ever resolved for.
    horizon_end = max(weeks_of_month(bm, mode, start_dow=start_dow)[-1]
                      for bm in buckets)

    # (bucket_month, canvas, material_code, demand_month, qty, bucket_reason,
    #  whole_only)
    overflow: list[tuple[str, list[date], str, str, Decimal, str | None, bool]] = []

    # Minimum-lot surplus awaiting attribution, keyed by (code, the demand
    # month it was booked against). Stamped onto whichever line ends up
    # carrying that slice, wherever the search finally places it.
    surplus_pending: dict[tuple[str, str], Decimal] = {}

    def _take_surplus(code: str, demand_month: str, qty: Decimal) -> Decimal:
        key = (code, demand_month)
        pending = surplus_pending.get(key)
        if not pending:
            return Decimal("0")
        taken = pending if pending <= qty else qty
        remainder = pending - taken
        if remainder > 0:
            surplus_pending[key] = remainder
        else:
            surplus_pending.pop(key, None)
        return taken

    for bucket_month in sorted(buckets):
        members = buckets[bucket_month]
        # Design §2.0 ④: the canvas is the owning month's FULL 4/5 weeks, so
        # P2 levelling and P3 one-product-per-week have somewhere to operate.
        # Only the past is excluded. Never empty: every member's target week
        # is one of this month's own weeks and is >= `current`.
        canvas = [w for w in weeks_of_month(bucket_month, mode, start_dow=start_dow)
                  if w >= current]
        per_week = [limits_for_week(w) for w in canvas]
        preloaded = [locked_by_week.get(w, {}) for w in canvas]

        contributions: dict[str, list[list]] = {}
        for item in sorted(members, key=lambda d: (d.material_code, d.demand_month)):
            contributions.setdefault(item.material_code, []).append(
                [item.demand_month, item.qty])

        # The floor a product falls back to in THIS bucket when it has no
        # rule of its own -- the same resolution `pack_bucket` performs, kept
        # in step deliberately: rounding up to one number and packing against
        # another would produce batches the packer then re-levels away.
        open_here = [i for i, wk in enumerate(per_week) if _week_can_host(wk)]
        bucket_floors = [per_week[i].min_output_qty for i in open_here
                         if per_week[i].min_output_qty is not None]
        bucket_floor = max(bucket_floors) if bucket_floors else None

        def _lot_for(code: str) -> Decimal | None:
            if min_lots is not None and code in min_lots:
                return min_lots[code]
            return bucket_floor

        merged = []
        rounded_codes: set[str] = set()
        for code, parts in sorted(contributions.items()):
            total = sum((q for _, q in parts), Decimal("0"))
            lot = _lot_for(code)
            if lot is not None and Decimal("0") < total < lot:
                # Round up to a whole batch. The excess is booked against the
                # LATEST demand month in this bucket: it is made together
                # with that month's own quantity and, being surplus, is what
                # later months draw on first.
                surplus = lot - total
                parts[-1][1] += surplus
                surplus_pending[(code, parts[-1][0])] = surplus
                rounded_codes.add(code)
                total = lot
            merged.append(BucketItem(code, parts[0][0], total))

        # ④ Pack the bucket.
        packed = pack_bucket(merged, canvas, per_week, preloaded=preloaded,
                             min_lots=min_lots)
        target_pos = {month: canvas.index(week) for month, week in targets.items()
                      if week in canvas}

        bucket_overflow: list[tuple] = []
        for code, parts in sorted(contributions.items()):
            queue = [list(part) for part in parts]       # ascending demand month
            produced = sorted(
                (l for l in packed if l.material_code == code and not l.capacity_gap),
                key=lambda l: l.plan_week_start)
            # Gap lines are pinned to a week and are NOT production; they
            # never touch the ledger and never count towards a week's SKUs.
            gaps = [l for l in packed if l.material_code == code and l.capacity_gap]

            for line in produced:
                here = canvas.index(line.plan_week_start)
                for demand_month, take in _peel(queue, line.qty):
                    early = max(0, target_pos[demand_month] - here)
                    if not _placement_allowed(
                            line.plan_week_start, demand_month,
                            shelf_life_months.get(code), safety_margin_fraction,
                            early):
                        # Shelf life will not carry this slice as far back as
                        # the packer put it. It is NOT a shortfall yet -- hand
                        # it to the backward walk, which starts at the last
                        # week of this very bucket and will therefore re-offer
                        # it every week the bucket has left, its own target
                        # week included (where it is not early at all and the
                        # gate does not apply). Declaring the gap here instead
                        # reported a shortfall against an EMPTY target week.
                        bucket_overflow.append((
                            bucket_month, canvas, code, demand_month, take,
                            f"the week of {line.plan_week_start.isoformat()} is earlier "
                            f"than shelf life allows for {demand_month} demand",
                            code in rounded_codes))
                        continue
                    lines.append(WeeklyLine(
                        material_code=code, demand_month=demand_month,
                        plan_week_start=line.plan_week_start,
                        plan_week_month=owning_month(line.plan_week_start, mode,
                                                     start_dow=start_dow),
                        qty=_tidy(take),
                        surplus_qty=_take_surplus(code, demand_month, take),
                        below_min_lot=line.below_min_lot,
                        # Inside the demand's OWN bucket month, so never a
                        # pre-build however early in the month it sits --
                        # spreading across the month is levelling, which is
                        # the entire reason the canvas is the whole month.
                        # `weeks_early` still records the real week distance
                        # from the target: the shelf-life gate needs it, and
                        # so does anyone reading the line in detail.
                        is_prebuild=False, weeks_early=early,
                        prebuild_reason=line.prebuild_reason,
                        lead_shortfall=clamped[demand_month]))
                    ledger.setdefault(line.plan_week_start, _WeekLoad()).commit(code, take)

            for gap in gaps:
                for demand_month, take in _peel(queue, gap.qty):
                    bucket_overflow.append((bucket_month, canvas, code, demand_month,
                                            take, gap.prebuild_reason,
                                            code in rounded_codes))

            if queue:
                # `pack_bucket` conserves exactly (produced + gap == input),
                # so the queue must be empty here. If it ever is not, some
                # net requirement has gone missing -- the one failure mode
                # this module refuses to have quietly.
                raise RuntimeError(
                    f"pack_bucket returned less than it was given for {code} in bucket "
                    f"{bucket_month}: {queue} left unattributed")

        overflow.extend(sorted(bucket_overflow, key=lambda o: (o[3], o[2])))

    # ⑤ / ⑥ Cross-bucket pre-build. Every bucket is packed before any
    #        overflow runs, so a bucket's own demand always outranks another
    #        bucket's pre-build in its weeks; overflow is then resolved in
    #        ascending bucket order, so earlier demand claims earlier weeks
    #        first.
    for bucket_month, canvas, code, demand_month, qty, bucket_reason, whole_only in overflow:
        target = targets[demand_month]
        shelf_life = shelf_life_months.get(code)
        # Ascending timeline, extended backwards one REAL week at a time.
        # Never `week - timedelta(weeks=1)`: under `month_fixed` weeks are
        # not all 7 days long, so only `shift_weeks` stays on the grid.
        #
        # The walk starts at the LAST canvas week, not at the target and not
        # outside the bucket: a bucket that still has idle open weeks (which
        # `pack_bucket` may deliberately leave -- its objective is unmet
        # demand, not week occupancy) must have them offered to this
        # quantity before any of it is called a shortfall. Reporting a
        # shortfall a planner could fix by hand inside that very month is
        # how a plan loses its reader.
        timeline = list(canvas)
        pos = timeline.index(target)
        cursor = len(timeline) - 1
        remaining = qty
        blocked_by_shelf_life = False
        stopper: date | None = None

        while remaining > 0:
            if cursor < 0:
                timeline.insert(0, shift_weeks(timeline[0], -1, mode, start_dow=start_dow))
                pos += 1
                cursor = 0
            week = timeline[cursor]
            if week < current:
                stopper = week
                break
            early = max(0, pos - cursor)
            if not _placement_allowed(week, demand_month, shelf_life,
                                      safety_margin_fraction, early):
                # The rule is monotone in the week: everything earlier fails
                # too, so stop rather than keep walking.
                blocked_by_shelf_life = True
                stopper = week
                break
            limits = limits_for_week(week)
            if _week_can_host(limits):
                load = ledger.setdefault(week, _WeekLoad())
                if load.sku_room(code, limits):
                    room = load.remaining(limits)
                    take = remaining if room is None else min(remaining, room)
                    if whole_only and take < remaining:
                        # A rounded batch placed in pieces puts every piece
                        # back under the lot size -- the very thing rounding
                        # up exists to avoid. Skip this week entirely.
                        take = Decimal("0")
                    if take > 0:
                        load.commit(code, take)
                        week_month = owning_month(week, mode, start_dow=start_dow)
                        crossed = week_month < bucket_month
                        lines.append(WeeklyLine(
                            material_code=code, demand_month=demand_month,
                            plan_week_start=week,
                            plan_week_month=week_month,
                            qty=_tidy(take),
                            is_prebuild=crossed, weeks_early=early,
                            surplus_qty=_take_surplus(code, demand_month, take),
                            prebuild_reason=(
                                _early_note(early, bucket_reason) if crossed
                                # Still inside the demand's own bucket month:
                                # not a pre-build, but it did not land where
                                # the packer first put it, and echoing the
                                # packer's "no week left" verbatim onto a line
                                # that DID find a week reads as a contradiction.
                                else f"re-placed within bucket {bucket_month}: {bucket_reason}"),
                            lead_shortfall=clamped[demand_month]))
                        remaining -= take
            # A full or closed week is stepped over, not stopped at: this is
            # a pre-build search (the month-based engine hops over full
            # months the same way), not one of `pack_bucket`'s contiguous
            # runs.
            cursor -= 1

        if remaining > 0:
            # Nothing earlier could take it. Producing LATE is worse than
            # producing on time and far better than not producing at all, so
            # walk forward from the end of the bucket to the end of the
            # horizon before calling this a shortfall. No shelf-life gate
            # applies going this way: later production is fresher, not
            # staler, when the demand month is already behind it.
            week = shift_weeks(canvas[-1], 1, mode, start_dow=start_dow)
            while remaining > 0 and week <= horizon_end:
                limits = limits_for_week(week)
                if _week_can_host(limits):
                    load = ledger.setdefault(week, _WeekLoad())
                    if load.sku_room(code, limits):
                        room = load.remaining(limits)
                        take = remaining if room is None else min(remaining, room)
                        if whole_only and take < remaining:
                            take = Decimal("0")
                        if take > 0:
                            load.commit(code, take)
                            lines.append(WeeklyLine(
                                material_code=code, demand_month=demand_month,
                                plan_week_start=week,
                                plan_week_month=owning_month(week, mode,
                                                             start_dow=start_dow),
                                qty=_tidy(take),
                                surplus_qty=_take_surplus(code, demand_month, take),
                                late_production=True,
                                prebuild_reason=(
                                    f"no week on or before {demand_month} could host this "
                                    f"production, so it is scheduled late"
                                    + (f": {bucket_reason}" if bucket_reason else "")),
                                lead_shortfall=clamped[demand_month]))
                            remaining -= take
                week = shift_weeks(week, 1, mode, start_dow=start_dow)

        if remaining > 0:
            # ⑥ Final shortfall: pinned to the target week -- where this
            #    quantity was supposed to be made -- and never flagged as a
            #    pre-build, because nothing moved.
            if blocked_by_shelf_life and shelf_life is None:
                why = (f"{code} has no shelf life on record, so it may never be "
                       f"pre-built out of bucket {bucket_month}")
            elif blocked_by_shelf_life:
                why = (f"shelf life bars pre-building {demand_month} demand as early as "
                       f"the week of {stopper.isoformat()}")
            else:
                # The only other way out of the walk: it reached the current
                # week. In the earliest bucket of the horizon that is
                # immediate -- there is no earlier bucket to pre-build into,
                # so this gap is already final rather than provisional.
                why = (f"pre-building stopped at the current week "
                       f"{current.isoformat()}; there is no earlier week to use")
            why += (f", and no week up to {horizon_end.isoformat()} could host it either")
            lines.append(WeeklyLine(
                material_code=code, demand_month=demand_month,
                plan_week_start=target,
                plan_week_month=owning_month(target, mode, start_dow=start_dow),
                qty=_tidy(remaining),
                surplus_qty=_take_surplus(code, demand_month, remaining),
                prebuild_reason=f"{bucket_reason}; {why}" if bucket_reason else why,
                shelf_life_ok=not blocked_by_shelf_life,
                capacity_gap=True,
                lead_shortfall=clamped[demand_month]))

    return _sorted_lines(_merge_same_slot(lines))
