"""MPS scheduling algorithm — pure-function tests (no DB, no clock).

Two layers, in the order the module defines them: `pack_bucket` (one
bucket's weeks) and `generate_mps` (the whole weekly pipeline).

**The month-based engine's cases were deleted here**, with the engine
itself, when `app/api/v1/mps.py` moved onto weeks: `generate_monthly_mps`,
its `PlannedLine` dataclass and the keyword-routing `generate_mps` shim over
the two are gone, so the 15 tests calling `generate_mps(..., lead_months=,
current_month=)` and `test_the_dispatcher_refuses_to_guess` had nothing left
to exercise (102 collected here before, 86 after). Nothing they covered went uncovered: in-month placement,
output/SKU overflow pre-build, the shelf-life gate (including unknown shelf
life), the lead shift and its clamp, the multi-hop skip over full periods,
locked lines consuming capacity, and gap vs. shelf-life-gap all have weekly
equivalents below -- on real dates, which is the entire point of the rework.
"""
from datetime import date
from decimal import Decimal

from app.services.mps_engine import (
    BucketItem, CapacityLimits, DemandItem, WeeklyLine, generate_mps, pack_bucket,
)
from app.services.mps_engine import _minus_months, _prebuild_allowed
from app.services.week_calendar import (
    owning_month, shift_weeks, week_start_of, weeks_of_month,
)


# ── Weekly bucket packing (weekly-MPS Task 4 brief, verbatim) ───────────────
#
# Single-bucket packing only: no lead time, no cross-bucket pre-build, no
# shelf-life gate (`generate_mps` below owns those). `pack_bucket` is a pure
# function that receives an
# already-resolved `list[date]` of week starts (it must never care which of
# `week_calendar.py`'s three modes produced them) and already-resolved
# `CapacityLimits`.

WEEKS = [date(2026, 8, 3), date(2026, 8, 10), date(2026, 8, 17), date(2026, 8, 24)]


def _items(**kv):
    return [BucketItem(code, "2026-08", Decimal(str(q))) for code, q in kv.items()]


def _by_week(lines):
    """Layout by week as `(material_code, qty, is_gap)` triples.

    The gap flag is part of the layout, not noise: without it a real
    production line and a shortfall line of the same quantity in the same
    week compare equal, and an assertion that two plans are "identical"
    cannot tell "produced 41" from "41 short".
    """
    out = {}
    for l in lines:
        out.setdefault(l.plan_week_start, []).append(
            (l.material_code, str(l.qty), l.capacity_gap))
    return {w: sorted(v) for w, v in out.items()}


def test_golden_case_from_the_business_owner():
    """A60 B20 C30 D30, cap 40/week, 4 weeks — the plan the planner drew by hand.

    W1 A40 | W2 A20+B20 | W3 C30 | W4 D30
    """
    limits = CapacityLimits(max_sku_count=None, max_output_qty=Decimal("40"),
                            min_output_qty=Decimal("20"))
    lines = pack_bucket(_items(A=60, B=20, C=30, D=30), WEEKS, limits)
    assert _by_week(lines) == {
        WEEKS[0]: [("A", "40", False)],
        WEEKS[1]: [("A", "20", False), ("B", "20", False)],
        WEEKS[2]: [("C", "30", False)],
        WEEKS[3]: [("D", "30", False)],
    }


def test_a_product_never_splits_when_it_fits_in_one_week():
    """朴素贪心会把 C 切成 20+10 —— 那既多一次换线又让 W2 变三个品。"""
    limits = CapacityLimits(None, Decimal("40"), Decimal("20"))
    lines = pack_bucket(_items(A=60, C=30), WEEKS, limits)
    c_weeks = {l.plan_week_start for l in lines if l.material_code == "C"}
    assert len(c_weeks) == 1


class TestSpreadRegime:
    """周数富余：摊到最低产能就停，剩下的周空着（D6：别为了填日历烧能源）。"""

    limits = CapacityLimits(None, Decimal("40"), Decimal("20"))

    def test_single_product_at_exactly_min_output_stays_in_one_week(self):
        lines = pack_bucket(_items(A=20), WEEKS, self.limits)
        assert len(lines) == 1
        assert str(lines[0].qty) == "20"

    def test_single_product_below_min_output_is_clamped_to_one_week(self):
        lines = pack_bucket(_items(A=10), WEEKS, self.limits)
        assert len(lines) == 1
        assert str(lines[0].qty) == "10"

    def test_single_product_at_twice_min_output_spreads_over_two_weeks(self):
        lines = pack_bucket(_items(A=40), WEEKS, self.limits)
        assert sorted(str(l.qty) for l in lines) == ["20", "20"]
        assert len({l.plan_week_start for l in lines}) == 2

    def test_capacity_floor_wins_over_min_output(self):
        """q=100, cap=40, min=50 → need_weeks=3 胜过 floor(100/50)=2。"""
        limits = CapacityLimits(None, Decimal("40"), Decimal("50"))
        lines = pack_bucket(_items(A=100), WEEKS, limits)
        assert len({l.plan_week_start for l in lines}) == 3
        assert all(Decimal(str(l.qty)) <= Decimal("40") for l in lines)

    def test_spare_weeks_go_to_the_heaviest_product(self):
        lines = pack_bucket(_items(A=60, B=20), WEEKS, self.limits)
        a_weeks = {l.plan_week_start for l in lines if l.material_code == "A"}
        b_weeks = {l.plan_week_start for l in lines if l.material_code == "B"}
        assert len(a_weeks) == 3 and len(b_weeks) == 1
        assert all(str(l.qty) == "20" for l in lines)


class TestPrinciples:
    """P1/P2/P3 的性质断言，跑一批构造输入。"""

    limits = CapacityLimits(max_sku_count=2, max_output_qty=Decimal("40"),
                            min_output_qty=Decimal("20"))
    CASES = [
        {"A": 60, "B": 20, "C": 30, "D": 30},
        {"A": 100, "B": 15},
        {"A": 20},
        {"A": 35, "B": 35, "C": 35},
        {"A": 5, "B": 5, "C": 5, "D": 5},
        {"A": 160},
        # sum(need_weeks) == len(weeks) exactly: a comfortable month (120 of
        # 160 available) that the old `>=` predicate sent down the tight path
        # and left with an idle W4. Under `>` it levels to 30/30/30/30.
        {"A": 60, "B": 60},
        # spare regime with span > 1 AND an awkward quantity, so `_level`'s
        # division and `qty - allocated` tail actually run. Without this every
        # CASE either goes tight (exact by construction) or has span 1 (which
        # short-circuits), and a per-week-rounding implementation would sail
        # through `test_nothing_is_silently_lost`.
        {"A": 70},
    ]

    def test_p1_each_product_occupies_a_contiguous_run(self):
        for case in self.CASES:
            lines = pack_bucket(_items(**case), WEEKS, self.limits)
            for code in case:
                idx = sorted(WEEKS.index(l.plan_week_start)
                             for l in lines if l.material_code == code)
                assert idx == list(range(idx[0], idx[0] + len(idx))), (case, code, idx)

    def test_p2_no_empty_week_while_an_earlier_week_could_have_been_thinned(self):
        for case in self.CASES:
            lines = pack_bucket(_items(**case), WEEKS, self.limits)
            used = {l.plan_week_start for l in lines}
            if len(used) == len(WEEKS):
                continue
            for l in lines:
                assert Decimal(str(l.qty)) < 2 * self.limits.min_output_qty, (case, l)

    def test_p3_weekly_sku_count_never_exceeds_the_hard_limit(self):
        for case in self.CASES:
            lines = pack_bucket(_items(**case), WEEKS, self.limits)
            per_week = {}
            for l in lines:
                per_week.setdefault(l.plan_week_start, set()).add(l.material_code)
            assert all(len(v) <= 2 for v in per_week.values()), case

    def test_per_week_capacity_is_never_exceeded(self):
        """Without this, a degenerate implementation passes every other
        property here: dump A=100 into W1 and spread B=15 over W2-W4 and you
        get contiguous runs, all four weeks used (so P2 short-circuits), one
        product per week, and nothing lost -- with W1 2.5x over capacity."""
        for case in self.CASES:
            lines = pack_bucket(_items(**case), WEEKS, self.limits)
            per_week = {}
            for l in lines:
                if l.capacity_gap:
                    continue           # a gap books no capacity anywhere
                per_week[l.plan_week_start] = (
                    per_week.get(l.plan_week_start, Decimal("0")) + Decimal(str(l.qty)))
            assert all(v <= self.limits.max_output_qty for v in per_week.values()), \
                (case, per_week)

    def test_nothing_is_silently_lost(self):
        for case in self.CASES:
            lines = pack_bucket(_items(**case), WEEKS, self.limits)
            for code, q in case.items():
                planned = sum((Decimal(str(l.qty)) for l in lines
                               if l.material_code == code), Decimal("0"))
                gap = sum((Decimal(str(l.qty)) for l in lines
                           if l.material_code == code and l.capacity_gap), Decimal("0"))
                assert planned == Decimal(str(q)), (case, code, planned)
                del gap  # 缺口行也计入总量，只是带标记


class TestPackBucketEdgeCases:
    """Cases the Task 4 brief left open; each one pins down a decision that
    `pack_bucket`'s docstring documents, so the decision cannot drift
    silently later."""

    limits = CapacityLimits(None, Decimal("40"), Decimal("20"))

    def test_equal_quantities_break_the_tie_by_material_code(self):
        # C and D are both 30; C must take the earlier week regardless of the
        # order they arrive in. (Input order deliberately reversed here.)
        lines = pack_bucket(_items(D=30, C=30, A=60, B=20), WEEKS, self.limits)
        placed = {l.material_code: l.plan_week_start for l in lines
                  if l.material_code in ("C", "D")}
        assert placed == {"C": WEEKS[2], "D": WEEKS[3]}

    def test_product_bigger_than_the_whole_bucket_gaps_the_remainder(self):
        # 200 against 4 x 40 = 160 of capacity: 160 is produced, 40 surfaces
        # as an explicit gap pinned to the last week the run occupied.
        lines = pack_bucket(_items(A=200), WEEKS, self.limits)
        produced = [l for l in lines if not l.capacity_gap]
        gaps = [l for l in lines if l.capacity_gap]
        assert [str(l.qty) for l in produced] == ["40"] * 4
        assert len(gaps) == 1
        assert str(gaps[0].qty) == "40" and gaps[0].plan_week_start == WEEKS[3]
        assert gaps[0].prebuild_reason is not None
        # nothing silently lost: gap lines still carry their qty
        assert sum((l.qty for l in lines), Decimal("0")) == Decimal("200")

    def test_zero_and_negative_quantities_are_dropped(self):
        lines = pack_bucket(_items(A=0, B=30, Z=-5), WEEKS, self.limits)
        assert [l.material_code for l in lines] == ["B"]

    def test_bucket_with_no_weeks_refuses_to_drop_demand(self):
        import pytest
        with pytest.raises(ValueError):
            pack_bucket(_items(A=30), [], self.limits)
        assert pack_bucket([], [], self.limits) == []

    def test_max_sku_count_can_block_the_leftover_packing_step(self):
        # One SKU per week: A(60) takes W1+W2, C and D take W3/W4, and B(20)
        # then has nowhere to go -- W2 has the qty room but not the SKU room.
        limits = CapacityLimits(1, Decimal("40"), Decimal("20"))
        lines = pack_bucket(_items(A=60, B=20, C=30, D=30), WEEKS, limits)
        per_week = {}
        for l in lines:
            if not l.capacity_gap:
                per_week.setdefault(l.plan_week_start, set()).add(l.material_code)
        assert all(len(v) <= 1 for v in per_week.values())
        gaps = [l for l in lines if l.capacity_gap]
        assert [(l.material_code, str(l.qty)) for l in gaps] == [("B", "20")]

    def test_max_sku_count_below_one_gaps_everything_even_in_the_spare_regime(self):
        # A(30) alone is the spare regime (need_weeks 1 < 4 weeks), which lays
        # out one product per week and so has no reason to look at the SKU
        # ceiling -- it must still refuse to breach a ceiling of 0.
        limits = CapacityLimits(0, Decimal("40"), Decimal("20"))
        lines = pack_bucket(_items(A=30), WEEKS, limits)
        assert all(l.capacity_gap for l in lines)
        assert sum((l.qty for l in lines), Decimal("0")) == Decimal("30")

    def test_non_positive_output_cap_gaps_everything_without_dividing_by_zero(self):
        limits = CapacityLimits(None, Decimal("0"), Decimal("20"))
        lines = pack_bucket(_items(A=30), WEEKS, limits)
        assert all(l.capacity_gap for l in lines)
        assert sum((l.qty for l in lines), Decimal("0")) == Decimal("30")

    def test_no_min_output_floor_means_no_spreading_beyond_need_weeks(self):
        limits = CapacityLimits(None, Decimal("40"), None)
        lines = pack_bucket(_items(A=40), WEEKS, limits)
        assert len(lines) == 1 and str(lines[0].qty) == "40"

    def test_two_oversized_products_pack_tight_and_leave_a_week_for_a_third(self):
        # A(60) leaves W2 half empty; B(60) must resume there rather than
        # skipping to the next empty week, or C(30) would have no week left
        # and would become a phantom gap despite the bucket having room.
        lines = pack_bucket(_items(A=60, B=60, C=30), WEEKS, self.limits)
        assert not any(l.capacity_gap for l in lines)
        assert _by_week(lines) == {
            WEEKS[0]: [("A", "40", False)],
            WEEKS[1]: [("A", "20", False), ("B", "20", False)],
            WEEKS[2]: [("B", "40", False)],
            WEEKS[3]: [("C", "30", False)],
        }

    def test_levelling_stays_exact_at_awkward_quantities(self):
        limits = CapacityLimits(None, Decimal("500000"), Decimal("1"))
        q = Decimal("1234567.891")
        lines = pack_bucket([BucketItem("A", "2026-08", q)], WEEKS, limits)
        assert sum((l.qty for l in lines), Decimal("0")) == q

    def test_two_items_of_the_same_material_count_as_one_sku(self):
        # Two demand months feeding one bucket (what Task 5 will do): they
        # are two independent runs but only ever one SKU on the floor.
        limits = CapacityLimits(1, Decimal("40"), Decimal("20"))
        lines = pack_bucket(
            [BucketItem("A", "2026-08", Decimal("30")),
             BucketItem("A", "2026-09", Decimal("30"))], WEEKS, limits)
        assert not any(l.capacity_gap for l in lines)
        assert {l.demand_month for l in lines} == {"2026-08", "2026-09"}


class TestPerWeekLimits:
    """`resolve_limits_for_week` resolves capacity PER WEEK, so `pack_bucket`
    accepts a per-week sequence as well as one bucket-wide value. This is what
    makes the design's flagship example -- "week 32 is down for maintenance,
    max_output_qty = 0" -- expressible at all: such a week is skipped as a
    placement target and its production moves elsewhere, rather than turning
    the whole bucket into a shortfall."""

    OPEN = CapacityLimits(None, Decimal("40"), Decimal("20"))
    SHUT = CapacityLimits(None, Decimal("0"), Decimal("20"))

    def test_a_uniform_sequence_matches_a_single_value(self):
        single = pack_bucket(_items(A=60, B=20, C=30, D=30), WEEKS, self.OPEN)
        per_week = pack_bucket(_items(A=60, B=20, C=30, D=30), WEEKS, [self.OPEN] * 4)
        assert _by_week(single) == _by_week(per_week)

    def test_shutdown_week_is_scheduled_around_not_gapped_spare_regime(self):
        # Three 30s and four weeks: W3 shut, so the third product lands in W4
        # instead. Nothing is short -- 90 against 120 of open capacity.
        limits = [self.OPEN, self.OPEN, self.SHUT, self.OPEN]
        lines = pack_bucket(_items(A=30, B=30, C=30), WEEKS, limits)
        assert not any(l.capacity_gap for l in lines)
        assert WEEKS[2] not in {l.plan_week_start for l in lines}
        assert _by_week(lines) == {
            WEEKS[0]: [("A", "30", False)],
            WEEKS[1]: [("B", "30", False)],
            WEEKS[3]: [("C", "30", False)],
        }

    def test_shutdown_week_is_stepped_over_mid_run_tight_regime(self):
        # A=100 cannot fit one week, so it splits -- and must step OVER the
        # shutdown rather than stopping at it. 120 demanded, 120 open: no gap.
        limits = [self.OPEN, self.OPEN, self.SHUT, self.OPEN]
        lines = pack_bucket(_items(A=100, B=20), WEEKS, limits)
        assert not any(l.capacity_gap for l in lines)
        assert _by_week(lines) == {
            WEEKS[0]: [("A", "40", False)],
            WEEKS[1]: [("A", "40", False)],
            WEEKS[3]: [("A", "20", False), ("B", "20", False)],
        }

    def test_every_week_shut_gaps_everything(self):
        lines = pack_bucket(_items(A=30), WEEKS, [self.SHUT] * 4)
        assert all(l.capacity_gap for l in lines)
        assert sum((l.qty for l in lines), Decimal("0")) == Decimal("30")

    def test_a_sequence_of_the_wrong_length_is_refused(self):
        import pytest
        with pytest.raises(ValueError):
            pack_bucket(_items(A=30), WEEKS, [self.OPEN] * 3)

    def test_a_tighter_week_never_gets_more_than_it_can_take(self):
        # W2 is derated to 10. need_weeks is sized off the SMALLEST open cap,
        # so the levelling can never hand a week more than that week can hold.
        small = CapacityLimits(None, Decimal("10"), Decimal("5"))
        limits = [self.OPEN, small, self.OPEN, self.OPEN]
        lines = pack_bucket(_items(A=35), WEEKS, limits)
        by_week = {l.plan_week_start: Decimal(str(l.qty)) for l in lines
                   if not l.capacity_gap}
        for week, qty in by_week.items():
            assert qty <= limits[WEEKS.index(week)].max_output_qty, (week, qty)
        assert sum((l.qty for l in lines), Decimal("0")) == Decimal("35")


class TestRegimePredicate:
    """`sum(need_weeks) > len(weeks)` is tight; equality is SPARE.

    `need_weeks` is a ceiling, so its slack is not consumed capacity --
    treating equality as tight sends comfortable months down the packing
    path and idles the end of the month for no reason."""

    limits = CapacityLimits(None, Decimal("40"), Decimal("20"))

    def test_equality_goes_spare_and_uses_the_whole_month(self):
        # {A:60, B:60}: sum(need_weeks) = 2 + 2 = 4 == 4 weeks, but only 120
        # of the 160 available is actually demanded.
        lines = pack_bucket(_items(A=60, B=60), WEEKS, self.limits)
        assert _by_week(lines) == {
            WEEKS[0]: [("A", "30", False)],
            WEEKS[1]: [("A", "30", False)],
            WEEKS[2]: [("B", "30", False)],
            WEEKS[3]: [("B", "30", False)],
        }

    def test_strictly_over_goes_tight(self):
        # Adding C=30 makes sum(need_weeks) = 5 > 4: the month really is
        # over-full (150 of 160) and packing tight is correct.
        lines = pack_bucket(_items(A=60, B=60, C=30), WEEKS, self.limits)
        assert _by_week(lines) == {
            WEEKS[0]: [("A", "40", False)],
            WEEKS[1]: [("A", "20", False), ("B", "20", False)],
            WEEKS[2]: [("B", "40", False)],
            WEEKS[3]: [("C", "30", False)],
        }


def test_level_tail_never_exceeds_the_weekly_cap():
    """Rounding `base` DOWN would give 39.999/39.999/40.001 -- the tail a
    thousandth of a kilo over a hard ceiling, purely from a rounding choice.
    Rounding up makes the tail absorb a negative remainder instead."""
    limits = CapacityLimits(None, Decimal("40"), Decimal("35"))
    lines = pack_bucket([BucketItem("A", "2026-08", Decimal("119.999"))], WEEKS, limits)
    assert all(Decimal(str(l.qty)) <= Decimal("40") for l in lines), \
        [str(l.qty) for l in lines]
    assert sum((l.qty for l in lines), Decimal("0")) == Decimal("119.999")


def test_the_same_bucket_item_object_passed_twice_is_packed_twice():
    """`_pack_spare` used to key its bookkeeping on `id(item)`; `BucketItem`
    is frozen, so a caller reusing one object would have the two entries
    collide and the layout loop could run off the end of the week list."""
    item = BucketItem("A", "2026-08", Decimal("30"))
    lines = pack_bucket([item, item], WEEKS, CapacityLimits(None, Decimal("40"), Decimal("20")))
    assert sum((l.qty for l in lines), Decimal("0")) == Decimal("60")
    assert len({l.plan_week_start for l in lines}) == 2


def _open_weeks_of(per_week):
    """The weeks a per-week limits list leaves open, mirroring the engine's
    own `_week_can_host` rule (deliberately restated here rather than
    imported, so the test does not inherit a bug from the thing it tests)."""
    return [w for w, lim in zip(WEEKS, per_week)
            if not (lim.max_output_qty is not None and lim.max_output_qty <= 0)
            and not (lim.max_sku_count is not None and lim.max_sku_count < 1)]


class TestHeterogeneousCapacityNeverStrandsAnEmptyWeek:
    """A de-rated or shut week must MOVE production, never destroy it.

    `_fits_in_one_week` answers "may this be split?" using the LARGEST open
    week's cap, but placement then needs ONE SPECIFIC week to have the room.
    When the big week is already taken, an item could once neither split nor
    place and became a phantom gap -- with entirely empty (smaller) weeks
    sitting idle. Distinct from the fragment gap under uniform capacity,
    where an empty week is by definition a full-capacity week and so always
    still fits."""

    def test_the_reviewers_repro_schedules_everything(self):
        # caps 20 / 40 / 20 / 0. B(29) takes W2, the only week that fits it
        # whole. A(25) fits no single remaining week -- and must therefore
        # split across W1+W2 rather than gap while W1 and W3 stand empty.
        caps = [CapacityLimits(None, Decimal(c), Decimal("20"))
                for c in ("20", "40", "20", "0")]
        lines = pack_bucket(_items(A=25, B=29), WEEKS, caps)
        assert not any(l.capacity_gap for l in lines), _by_week(lines)
        assert sum((l.qty for l in lines), Decimal("0")) == Decimal("54")
        # per WEEK, not per line: two lines summing over one week's cap is
        # exactly the failure a per-line check would wave through.
        totals = {}
        for l in lines:
            totals[l.plan_week_start] = (
                totals.get(l.plan_week_start, Decimal("0")) + Decimal(str(l.qty)))
        for week, qty in totals.items():
            cap = caps[WEEKS.index(week)].max_output_qty
            assert qty <= cap, (week, qty, cap, _by_week(lines))

    def test_these_heterogeneous_scenarios_schedule_completely(self):
        """A REGRESSION TRIPWIRE, and the teeth are the no-gap assertion.

        Read the second loop honestly: all four scenarios currently schedule
        with ZERO gaps, so the `continue` fires every time and the idle-week
        assertion below it **never executes**. It is kept as a guard for the
        day one of these does gap. What actually holds the fix is the first
        assertion -- revert the last-resort split and scenarios 1 and 2 gap
        immediately.

        The idle-week property is NOT a universal invariant either: see
        `test_a_run_stops_at_a_full_week_and_starts_where_it_places_most`
        below, where a gap coexists with idle open weeks by design."""
        scenarios = [
            (("20", "40", "20", "0"), {"A": 25, "B": 29}),
            (("20", "40", "20", "0"), {"A": 25, "B": 29, "C": 15}),
            (("10", "40", "15", "40"), {"A": 30, "B": 38, "C": 12}),
            (("40", "5", "40", "5"), {"A": 35, "B": 35}),
        ]
        for cap_strs, case in scenarios:
            caps = [CapacityLimits(None, Decimal(c), Decimal("5")) for c in cap_strs]
            lines = pack_bucket(_items(**case), WEEKS, caps)
            assert not any(l.capacity_gap for l in lines), (cap_strs, case, _by_week(lines))
            assert sum((l.qty for l in lines), Decimal("0")) == Decimal(str(sum(case.values())))

        for cap_strs, case in scenarios:
            caps = [CapacityLimits(None, Decimal(c), Decimal("5")) for c in cap_strs]
            lines = pack_bucket(_items(**case), WEEKS, caps)
            if not any(l.capacity_gap for l in lines):
                continue                      # currently always taken
            used = {l.plan_week_start for l in lines if not l.capacity_gap}
            idle = [w for w in _open_weeks_of(caps) if w not in used]
            assert not idle, (cap_strs, case, _by_week(lines), idle)

    def test_a_last_resort_split_is_still_contiguous(self):
        caps = [CapacityLimits(None, Decimal(c), Decimal("20"))
                for c in ("20", "40", "20", "0")]
        lines = pack_bucket(_items(A=25, B=29), WEEKS, caps)
        open_weeks = _open_weeks_of(caps)
        for code in ("A", "B"):
            idx = sorted(open_weeks.index(l.plan_week_start) for l in lines
                         if l.material_code == code and not l.capacity_gap)
            assert idx == list(range(idx[0], idx[0] + len(idx))), (code, idx)


class TestContiguityIsOverOpenWeeks:
    """P1 redefined: a run steps over a closed week at no changeover cost,
    because the line is down anyway. `TestPrinciples.test_p1` measures
    indices against the natural WEEKS list and has no closed week in any of
    its CASES, so it cannot see this meaning at all -- this class is what
    actually holds it, on both the tight and the spare path."""

    OPEN = CapacityLimits(None, Decimal("40"), Decimal("20"))
    SHUT = CapacityLimits(None, Decimal("0"), Decimal("20"))
    DERATED = CapacityLimits(None, Decimal("20"), Decimal("20"))

    # W3 closed -> open-week ordinals are W1=0, W2=1, W4=2
    SCENARIOS = [
        # (per-week caps, case, which regime it lands in)
        ([OPEN, OPEN, SHUT, OPEN], {"A": 100, "B": 20}, "tight"),
        ([OPEN, OPEN, SHUT, OPEN], {"A": 30, "B": 30, "C": 30}, "spare"),
        ([OPEN, OPEN, SHUT, OPEN], {"A": 90}, "spare"),
        ([OPEN, OPEN, SHUT, OPEN], {"A": 60, "B": 40}, "spare"),
        ([OPEN, SHUT, OPEN, OPEN], {"A": 110, "B": 10}, "tight"),
        ([DERATED, OPEN, SHUT, OPEN], {"A": 25, "B": 29}, "tight"),
    ]

    def test_runs_are_contiguous_over_open_week_indices(self):
        for per_week, case, _regime in self.SCENARIOS:
            lines = pack_bucket(_items(**case), WEEKS, per_week)
            open_weeks = _open_weeks_of(per_week)
            for code in case:
                idx = sorted(open_weeks.index(l.plan_week_start) for l in lines
                             if l.material_code == code and not l.capacity_gap)
                assert idx == list(range(idx[0], idx[0] + len(idx))), \
                    (case, code, idx, _by_week(lines))

    def test_nothing_is_ever_placed_in_a_closed_week(self):
        for per_week, case, _regime in self.SCENARIOS:
            lines = pack_bucket(_items(**case), WEEKS, per_week)
            open_weeks = _open_weeks_of(per_week)
            for l in lines:
                assert l.plan_week_start in open_weeks, (case, l)

    def test_a_spare_block_steps_over_the_shutdown(self):
        # 90 over three open weeks: W1, W2 and W4, 30 each. The run is
        # contiguous over open weeks while skipping a calendar week entirely.
        lines = pack_bucket(_items(A=90), WEEKS, [self.OPEN, self.OPEN, self.SHUT, self.OPEN])
        assert _by_week(lines) == {
            WEEKS[0]: [("A", "30", False)],
            WEEKS[1]: [("A", "30", False)],
            WEEKS[3]: [("A", "30", False)],
        }

    def test_per_week_capacity_holds_under_closed_weeks(self):
        for per_week, case, _regime in self.SCENARIOS:
            lines = pack_bucket(_items(**case), WEEKS, per_week)
            totals = {}
            for l in lines:
                if l.capacity_gap:
                    continue
                totals[l.plan_week_start] = (
                    totals.get(l.plan_week_start, Decimal("0")) + Decimal(str(l.qty)))
            for week, qty in totals.items():
                cap = per_week[WEEKS.index(week)].max_output_qty
                assert cap is None or qty <= cap, (case, week, qty, cap)


def test_gap_in_an_all_closed_bucket_does_not_blame_a_closed_week():
    """Display-only, but it is exactly the situation someone would be
    debugging: pinning to weeks[-1] and reporting "under max_output_qty 0"
    is true and useless."""
    shut = CapacityLimits(None, Decimal("0"), Decimal("20"))
    lines = pack_bucket(_items(A=30), WEEKS, [shut] * 4)
    assert all(l.capacity_gap for l in lines)
    reason = lines[0].prebuild_reason
    assert "closed" in reason and "max_output_qty 0" not in reason, reason


def test_gap_is_pinned_to_an_open_week_not_a_closed_one():
    # W4 is shut and W1-W3 are single-SKU, so B(20) has nowhere to go. Its
    # gap must land on the last OPEN week, not on the shutdown.
    per_week = [CapacityLimits(1, Decimal("40"), Decimal("20"))] * 3 + \
               [CapacityLimits(1, Decimal("0"), Decimal("20"))]
    lines = pack_bucket(_items(A=40, B=20, C=40, D=40), WEEKS, per_week)
    gaps = [l for l in lines if l.capacity_gap]
    assert gaps, _by_week(lines)
    assert all(l.plan_week_start != WEEKS[3] for l in gaps), _by_week(lines)


def test_a_small_product_takes_an_empty_week_before_another_products_leftover():
    """Spec 2.2's ordering, which lost its only discriminating case when
    `{A:100, B:15}` moved to the spare regime under the `>` predicate.

    A(100) leaves W3 holding 20 with 20 free, and B(15) would fit in that
    leftover -- but W4 is entirely empty, and P3 prefers a week of one's own
    to sharing one. The opposite rule (leftover space first) would put B in
    W3 and leave W4 idle."""
    limits = CapacityLimits(None, Decimal("40"), Decimal("20"))
    lines = pack_bucket(_items(A=100, B=15, C=5), WEEKS, limits)
    assert _by_week(lines) == {
        WEEKS[0]: [("A", "40", False)],
        WEEKS[1]: [("A", "40", False)],
        WEEKS[2]: [("A", "20", False), ("C", "5", False)],
        WEEKS[3]: [("B", "15", False)],
    }


def test_a_run_stops_at_a_full_week_and_starts_where_it_places_most():
    """A run is one unbroken block, so where it BEGINS is the only lever --
    and it begins where it places the most, not at the earliest week with a
    scrap of room.

    B(40) takes W3, the only week that fits it whole. A(35) then fits no
    single week and falls back to a split. Starting at W1 would run
    W1(20) + W2(5) and stop dead at full W3 -- 25 placed, 10 short.
    Starting at W4 places 30 and is short only 5. Same P1, same single
    contiguous block, 5 t more product.

    Idle open weeks (here W1 and W2) alongside a gap are therefore a
    DELIBERATE outcome, not the phantom-gap bug: the alternative plan uses
    those weeks and makes less. Unmet demand is the objective; week
    occupancy is not."""
    caps = [CapacityLimits(None, Decimal(c), Decimal("1"))
            for c in ("20", "5", "40", "30")]
    lines = pack_bucket(_items(A=35, B=40), WEEKS, caps)

    gaps = [l for l in lines if l.capacity_gap]
    assert [(l.material_code, str(l.qty)) for l in gaps] == [("A", "5")], _by_week(lines)
    produced = [l for l in lines if not l.capacity_gap]
    assert sorted((l.material_code, str(l.qty)) for l in produced) == \
        [("A", "30"), ("B", "40")], _by_week(lines)
    assert {l.plan_week_start for l in produced} == {WEEKS[2], WEEKS[3]}
    assert sum((l.qty for l in lines), Decimal("0")) == Decimal("75")


def _fixed_policy_plan(items, per_week, split, fullest, weeks=None):
    """One fixed-policy plan through the engine's internals, replicating
    `pack_bucket`'s pre-flight. `(False, False)` is the conservative
    baseline -- exactly the behaviour before the last-resort split and the
    fullest-start selection existed."""
    from app.services import mps_engine as E

    weeks = WEEKS if weeks is None else weeks
    payload = [i for i in items if i.qty > 0]
    if not payload:
        return []
    ordered = sorted(payload, key=E._sort_key)
    open_weeks = [i for i, wk in enumerate(per_week) if E._week_can_host(wk)]
    if not open_weeks:
        return E._pack_tight(ordered, weeks, per_week, open_weeks)
    ref_cap = E._reference_cap(per_week, open_weeks)
    needs = [E._need_weeks(i.qty, ref_cap) for i in ordered]
    if sum(needs) > len(open_weeks):
        return E._pack_tight(ordered, weeks, per_week, open_weeks, split, fullest)
    floors = [per_week[i].min_output_qty for i in open_weeks
              if per_week[i].min_output_qty is not None]
    return E._pack_spare(ordered, weeks, per_week, open_weeks, needs,
                         max(floors) if floors else None)


def _unmet(lines):
    return sum((Decimal(str(l.qty)) for l in lines if l.capacity_gap), Decimal("0"))


class TestTheSplitMustEarnItsKeep:
    """The last-resort split is a bet that it reduces the shortfall. Under
    `max_sku_count=1` the bet can lose badly -- the sliver spilled into the
    next week consumes that week's only SKU slot and destroys the rest of
    its capacity for everyone else -- so `pack_bucket` evaluates the plan
    without the split too and keeps whichever leaves less demand unmet."""

    def test_a_sliver_that_poisons_a_single_sku_week_is_not_taken(self):
        # caps 40/50/40/40, one SKU per week. C(51) splits W1(40)+W2(11).
        # A(41) then fits no single week; splitting it W3(40)+W4(1) places
        # 41 but the 1 t sliver locks W4, gapping B(40) and D(12) -- 92
        # produced against 103 for simply leaving A short.
        per_week = [CapacityLimits(1, Decimal(c), Decimal("1"))
                    for c in ("40", "50", "40", "40")]
        items = _items(A=41, B=40, C=51, D=12)
        lines = pack_bucket(items, WEEKS, per_week)

        assert _by_week(lines) == {
            WEEKS[0]: [("C", "40", False)],
            WEEKS[1]: [("C", "11", False)],
            WEEKS[2]: [("B", "40", False)],
            # A is 41 SHORT here, not 41 produced -- the flag is what makes
            # this expectation say so.
            WEEKS[3]: [("A", "41", True), ("D", "12", False)],
        }
        assert [(l.material_code, str(l.qty)) for l in lines if l.capacity_gap] \
            == [("A", "41")]
        produced = sum((l.qty for l in lines if not l.capacity_gap), Decimal("0"))
        assert produced == Decimal("103")
        # ...which is exactly the conservative plan, and strictly better than
        # taking the split.
        assert _unmet(lines) == _unmet(_fixed_policy_plan(items, per_week, False, False))
        assert _unmet(lines) < _unmet(_fixed_policy_plan(items, per_week, True, False))

    def test_never_leaves_more_unmet_than_the_conservative_baseline(self):
        """The absolute bar: for ANY input, the chosen plan must not leave
        more demand unmet than the no-split, earliest-start baseline. It is
        structural -- that baseline is one of the four candidates
        `pack_bucket` evaluates -- and this pins it across the trigger
        profile that broke it (max_sku_count=1 with uneven weeks)."""
        cap_sets = [
            ("40", "40", "40", "40"),
            ("40", "50", "40", "40"),
            ("20", "40", "20", "0"),
            ("20", "5", "40", "30"),
            ("10", "40", "15", "40"),
            ("50", "5", "50", "5"),
        ]
        cases = [
            {"A": 41, "B": 40, "C": 51, "D": 12},
            {"A": 25, "B": 29},
            {"A": 35, "B": 40},
            {"A": 60, "B": 20, "C": 30, "D": 30},
            {"A": 100, "B": 15},
            {"A": 51, "B": 49, "C": 7},
        ]
        for sku in (1, 2, 3, None):
            for caps in cap_sets:
                for min_out in ("1", "20"):
                    per_week = [CapacityLimits(sku, Decimal(c), Decimal(min_out))
                                for c in caps]
                    for case in cases:
                        items = _items(**case)
                        actual = pack_bucket(items, WEEKS, per_week)
                        baseline = _fixed_policy_plan(items, per_week, False, False)
                        assert _unmet(actual) <= _unmet(baseline), \
                            (sku, caps, min_out, case,
                             _by_week(actual), _by_week(baseline))
                        # and nothing is ever lost, whichever policy wins
                        for code, q in case.items():
                            planned = sum((Decimal(str(l.qty)) for l in actual
                                           if l.material_code == code), Decimal("0"))
                            assert planned == Decimal(str(q)), (sku, caps, case, code)

    def test_the_conservative_plan_wins_every_tie(self):
        """A REAL tie -- same unmet, different layouts -- broken conservatively.

        Three uniform 50 t weeks, four SKUs allowed, no floor. E(103) fills
        W1, W2 and 3 t of W3. Every strategy then leaves exactly 66 unmet
        and produces exactly 150, but they get there differently:

          conservative (False, False): C(46) and A(1) produced WHOLE,
                                       B, D and F short
          with the split (True, *):    F fragmented into a 47 t partial
                                       batch, and C, A, B, D ALL short

        The split spent a changeover to convert "a whole C ships" into
        "47/48ths of an F, which nobody can ship". Equal shortfall must
        therefore keep products whole -- i.e. keep the conservative plan.

        (The version of this test before fix round 4 asserted the property
        over inputs on which all four strategies produce the SAME plan, so
        it could not have failed however the tie was broken -- and it did
        not fail while the tie was in fact being broken the wrong way.)
        """
        weeks = WEEKS[:3]
        per_week = [CapacityLimits(4, Decimal("50"), Decimal("1"))] * 3
        items = _items(A=1, B=10, C=46, D=8, E=103, F=48)

        plans = {(split, fullest):
                 _fixed_policy_plan(items, per_week, split, fullest, weeks=weeks)
                 for split in (False, True) for fullest in (False, True)}

        # It genuinely is a tie: all four leave the same demand unmet...
        assert {_unmet(p) for p in plans.values()} == {Decimal("66")}
        conservative = plans[(False, False)]
        # ...and they are genuinely different plans, so the tie-break decides.
        assert _by_week(plans[(True, False)]) != _by_week(conservative)

        assert _by_week(pack_bucket(items, weeks, per_week)) == \
            _by_week(conservative)

        # What the conservative layout buys, and what the split would cost.
        produced = {(l.material_code, str(l.qty)) for l in conservative
                    if not l.capacity_gap}
        assert ("C", "46") in produced and ("A", "1") in produced
        split_produced = {(l.material_code, str(l.qty))
                          for l in plans[(True, False)] if not l.capacity_gap}
        assert ("F", "47") in split_produced      # a 48 t product, 1 t short
        assert ("C", "46") not in split_produced


def test_the_last_resort_split_also_pays_under_uniform_capacity():
    """The split is not only a heterogeneous-capacity fix.

    Uniform 40 t weeks. A(59) takes W1+W2(19), C(37) takes W3, B(33) takes
    W4 -- and D(32) then finds no empty week and no single leftover big
    enough, so the conservative plan leaves all 32 unmet. Spread across the
    three leftovers (21 + 3 + 7) it is short only 1.

    (Fullest-start, by contrast, IS heterogeneous-only: measured over 60,000
    uniform-capacity buckets it never once beat the earliest-start baseline
    on its own, because under uniform capacity the largest items are placed
    first into empty weeks, so earliest and fullest coincide.)"""
    limits = [CapacityLimits(None, Decimal("40"), Decimal("1"))] * 4
    items = _items(A=59, B=33, C=37, D=32)
    lines = pack_bucket(items, WEEKS, limits)

    baseline = _fixed_policy_plan(items, limits, split=False, fullest=False)
    assert _unmet(baseline) == Decimal("32")
    assert _unmet(lines) == Decimal("1")
    assert sum((l.qty for l in lines), Decimal("0")) == Decimal("161")
    # D is one contiguous run despite being spread over three weeks
    d_weeks = sorted(WEEKS.index(l.plan_week_start) for l in lines
                     if l.material_code == "D" and not l.capacity_gap)
    assert d_weeks == list(range(d_weeks[0], d_weeks[0] + len(d_weeks))), d_weeks


def test_uniform_capacity_never_strands_an_open_week_beside_a_gap():
    """The other half of the claim above, as a property rather than prose:
    under uniform capacity a gap never coexists with an entirely unused
    week. Verified over 30,000 randomised uniform buckets (0 occurrences);
    this pins the deterministic core of that sweep."""
    for cap in ("10", "20", "40", "50"):
        for sku in (1, 2, 3, None):
            limits = [CapacityLimits(sku, Decimal(cap), Decimal("1"))] * 4
            for case in ({"A": 59, "B": 33, "C": 37, "D": 32},
                         {"A": 41, "B": 40, "C": 51, "D": 12},
                         {"A": 200}, {"A": 55, "B": 33, "C": 40, "D": 42},
                         {"A": 60, "B": 20, "C": 30, "D": 30}):
                lines = pack_bucket(_items(**case), WEEKS, limits)
                if not any(l.capacity_gap for l in lines):
                    continue
                used = {l.plan_week_start for l in lines if not l.capacity_gap}
                idle = [w for w in WEEKS if w not in used]
                assert not idle, (cap, sku, case, _by_week(lines), idle)


# ══════════════════════════════════════════════════════════════════════════
# Weekly pipeline (weekly-MPS Task 5): lead shift, bucketing, cross-bucket
# pre-build, shelf-life gate
# ══════════════════════════════════════════════════════════════════════════
#
# The first seven cases are verbatim from the Task 5 brief. Everything after
# `TestWeeklyPipelineEdges` is regression cover for a decision this task had
# to make that the brief left open (or that Task 4's handover flagged).


def test_lead_zero_reproduces_no_shift():
    """lead=0 时目标周就是需求月末周，产量不许溢出到需求月之外。

    **本条相对 brief 原文已放宽**，依据 spec `dc9a78e`：原文断言
    `{plan_week_start} == {末周}`，那要求画布只有一周；而画布是整月
    （§2.0 ④）才是对的，否则四个活跃桶里有四个只剩一周、P2/P3 结构性
    失效。月引擎的放置单位本来就是月，所以"不提前"= 落在需求月这个桶
    之内，不是"只能落在末周"。"""
    lines = generate_mps(
        demands=[DemandItem("A", "2026-10", Decimal("30"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={"A": 24}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=0, current_week=date(2026, 8, 3), mode="iso_thursday",
    )
    assert lines
    assert {l.plan_week_month for l in lines} == {"2026-10"}
    assert not any(l.capacity_gap for l in lines)
    # ...and nothing spilled into a week owned by an earlier month.
    assert min(l.plan_week_start for l in lines) >= weeks_of_month(
        "2026-10", "iso_thursday")[0]


def test_lead_four_weeks_moves_the_target_back_four_weeks():
    target = shift_weeks(weeks_of_month("2026-10", "iso_thursday")[-1], -4, "iso_thursday")
    lines = generate_mps(
        demands=[DemandItem("A", "2026-10", Decimal("30"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={"A": 24}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=4, current_week=date(2026, 8, 3), mode="iso_thursday",
    )
    assert {l.plan_week_start for l in lines} == {target}


def test_lead_clamped_to_current_week_flags_shortfall():
    lines = generate_mps(
        demands=[DemandItem("A", "2026-08", Decimal("30"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={"A": 24}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=12, current_week=date(2026, 8, 24), mode="iso_thursday",
    )
    assert all(l.plan_week_start >= date(2026, 8, 24) for l in lines)
    assert any(l.lead_shortfall for l in lines)


def test_weeks_early_counts_prebuild_only_not_the_lead_itself():
    """lead 造成的提前不算 weeks_early，否则每行都写着"提前 4 周"，告警就废了。"""
    lines = generate_mps(
        demands=[DemandItem("A", "2026-10", Decimal("30"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={"A": 24}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=4, current_week=date(2026, 8, 3), mode="iso_thursday",
    )
    assert all(l.weeks_early == 0 and not l.is_prebuild for l in lines)


def test_overflow_moves_earlier_and_marks_prebuild():
    """需求超过目标月总产能 → 向更早的周溢出，标 is_prebuild 与 weeks_early。"""
    lines = generate_mps(
        demands=[DemandItem("A", "2026-10", Decimal("300"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={"A": 24}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=0, current_week=date(2026, 6, 1), mode="iso_thursday",
    )
    assert sum(Decimal(str(l.qty)) for l in lines) == Decimal("300")
    assert any(l.is_prebuild and l.weeks_early > 0 for l in lines)


def test_shelf_life_uses_real_date_difference_not_4_33_weeks_per_month():
    """保质期 3 个月、安全余量 1/3 → 允许提前 ≈ 61 天。
    62 天前的那一周必须被判为不可用，而"3×4.33=13 周=91 天"的近似会放它过去。"""
    lines = generate_mps(
        demands=[DemandItem("A", "2026-10", Decimal("400"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={"A": 3}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=0, current_week=date(2026, 1, 5), mode="iso_thursday",
    )
    demand_start = date(2026, 10, 1)
    for l in lines:
        if not l.capacity_gap:
            assert (demand_start - l.plan_week_start).days <= 62, l


def test_missing_shelf_life_means_never_movable():
    lines = generate_mps(
        demands=[DemandItem("A", "2026-10", Decimal("300"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=0, current_week=date(2026, 1, 5), mode="iso_thursday",
    )
    assert any(l.capacity_gap for l in lines)
    assert all(not l.is_prebuild for l in lines)


# ── Task 5's own decisions, each with a witness ─────────────────────────────

_WEEKLY = "iso_thursday"


def _cap(cap="40", min_out="20", sku=None):
    """A `limits_for_week` that answers the same thing for every week."""
    return lambda w: CapacityLimits(
        sku, None if cap is None else Decimal(cap),
        None if min_out is None else Decimal(min_out))


def _run(demands, limits=None, shelf=None, margin="0.3333", lead=4,
         now=date(2026, 8, 3), mode=_WEEKLY, locked=None):
    return generate_mps(
        demands=demands, limits_for_week=limits or _cap(),
        shelf_life_months={"A": 24, "B": 24, "C": 24} if shelf is None else shelf,
        safety_margin_fraction=Decimal(margin), lead_weeks=lead,
        current_week=now, mode=mode, locked=locked)


def _total(lines):
    return sum((Decimal(str(l.qty)) for l in lines), Decimal("0"))


def test_minus_months_clamps_the_day_of_month():
    """31 Mar minus one month is the end of February, not 2 or 3 March.

    Rolling over would make the shelf-life horizon one to three days LONGER
    than the calendar allows, in the direction that lets stale product
    through."""
    assert _minus_months(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert _minus_months(date(2024, 3, 31), 1) == date(2024, 2, 29)   # leap
    assert _minus_months(date(2026, 1, 31), 1) == date(2025, 12, 31)
    assert _minus_months(date(2026, 1, 15), 13) == date(2024, 12, 15)
    assert _minus_months(date(2026, 5, 1), 3) == date(2026, 2, 1)


def test_shelf_life_rejects_a_week_the_4_33_approximation_would_admit():
    """The reason design §2.6 bans "4.33 weeks per month", as a number.

    Three months of shelf life ending 2026-05-01 is 89 REAL days -- February
    is short -- while 3 x 4.33 x 7 is 90.93. At a 1/3 safety margin that is
    59 allowed days against the approximation's 60, and the ISO week
    starting 2026-03-02 sits at exactly 60. The approximation plans a whole
    week of production that the calendar says expires before it ships."""
    demand_start, week = date(2026, 5, 1), date(2026, 3, 2)
    assert week.weekday() == 0 and (demand_start - week).days == 60

    real_horizon = (demand_start - _minus_months(demand_start, 3)).days
    approx_horizon = int(Decimal("3") * Decimal("4.33") * Decimal("7"))
    assert (real_horizon, approx_horizon) == (89, 90)

    margin = Decimal("0.3333")
    assert int(Decimal(real_horizon) * (1 - margin)) == 59
    assert int(Decimal(approx_horizon) * (1 - margin)) == 60      # lets 60 through

    assert _prebuild_allowed(week, "2026-05", 3, margin) is False
    assert _prebuild_allowed(date(2026, 3, 9), "2026-05", 3, margin) is True

    # ...and end to end: nothing is ever planned into that week.
    lines = _run([DemandItem("A", "2026-05", Decimal("400"))],
                 shelf={"A": 3}, lead=0, now=date(2026, 1, 5))
    assert _total(lines) == Decimal("400")
    for l in lines:
        if not l.capacity_gap:
            assert (demand_start - l.plan_week_start).days <= 59, l
    assert not any(l.plan_week_start == week and not l.capacity_gap for l in lines)


def test_a_lead_longer_than_shelf_life_falls_back_to_a_legal_later_week():
    """The lead shift can outrun shelf life on its own -- the weekly form of
    the month engine's `test_shelf_life_shorter_than_lead_is_a_gap`. A
    one-month shelf life at a 1/3 margin allows 20 days before 2026-10-01,
    and an 8-week lead targets the week of 2026-08-31, 31 days out.

    **That is not a shortfall.** The bucket (2026-09) also holds weeks LATER
    than the target, which are closer to the demand month and therefore
    legal. Producing late is worse than producing on the lead and enormously
    better than not producing at all, so the plan falls back to 2026-09-21
    (10 days out). Reporting a gap here -- which an earlier revision of this
    engine did -- is the same false shortfall as the in-bucket one above."""
    lines = _run([DemandItem("A", "2026-10", Decimal("30"))], shelf={"A": 1},
                 lead=8, now=date(2026, 1, 5))
    assert [(l.plan_week_start, l.capacity_gap) for l in lines] == [
        (date(2026, 9, 21), False)]
    assert (date(2026, 10, 1) - lines[0].plan_week_start).days == 10
    # The same lead is unremarkable once shelf life is long enough.
    ok = _run([DemandItem("A", "2026-10", Decimal("30"))], shelf={"A": 24},
              lead=8, now=date(2026, 1, 5))
    assert not any(l.capacity_gap for l in ok)
    assert {l.plan_week_start for l in ok} == {date(2026, 8, 31)}   # on the lead


def test_a_bucket_with_no_week_shelf_life_allows_is_a_gap():
    """The other side of that fallback: when not even the LAST week of the
    bucket is close enough to the demand month, there is nowhere legal to go
    and the shortfall is real. A 1-month shelf life at a 0.7 margin allows
    9 days; the bucket's latest week is 10 days out."""
    lines = _run([DemandItem("A", "2026-10", Decimal("30"))], shelf={"A": 1},
                 margin="0.7", lead=8, now=date(2026, 1, 5))
    assert [(l.capacity_gap, l.shelf_life_ok, l.is_prebuild) for l in lines] == [
        (True, False, False)]
    assert _total(lines) == Decimal("30")
    assert "shelf life" in lines[0].prebuild_reason


def test_two_demand_months_of_one_material_are_merged_into_one_run():
    """`pack_bucket` guarantees contiguity per BucketItem, not per material,
    and lead-shifting routinely puts two demand months of one material in
    one bucket. Unmerged, A comes out as two runs with C wedged between --
    two extra cleandowns, which is exactly what P1 exists to prevent."""
    demands = [DemandItem("A", "2026-10", Decimal("60")),
               DemandItem("C", "2026-10", Decimal("50")),
               DemandItem("A", "2026-11", Decimal("40"))]
    canvas = weeks_of_month("2026-10", _WEEKLY)
    assert shift_weeks(weeks_of_month("2026-10", _WEEKLY)[-1], -4, _WEEKLY) == canvas[0]
    assert owning_month(shift_weeks(weeks_of_month("2026-11", _WEEKLY)[-1], -4, _WEEKLY),
                        _WEEKLY) == "2026-10"          # both land in one bucket

    # What NOT merging would produce, straight from the packer.
    unmerged = pack_bucket(
        [BucketItem("A", "2026-10", Decimal("60")),
         BucketItem("C", "2026-10", Decimal("50")),
         BucketItem("A", "2026-11", Decimal("40"))],
        canvas, CapacityLimits(None, Decimal("40"), Decimal("20")))
    split = sorted({canvas.index(l.plan_week_start) for l in unmerged
                    if l.material_code == "A" and not l.capacity_gap})
    assert split != list(range(split[0], split[-1] + 1)), split   # two runs

    lines = _run(demands)
    for code in ("A", "C"):
        weeks = sorted({canvas.index(l.plan_week_start) for l in lines
                        if l.material_code == code and not l.capacity_gap})
        assert weeks == list(range(weeks[0], weeks[-1] + 1)), (code, weeks)
    for code, month, qty in (("A", "2026-10", "60"), ("A", "2026-11", "40"),
                             ("C", "2026-10", "50")):
        assert _total([l for l in lines if l.material_code == code
                       and l.demand_month == month]) == Decimal(qty)


def test_the_shortfall_lands_on_the_latest_demand_month_not_pro_rata():
    """Earliest demand month satisfied first. "November is short 50" is
    actionable; "everything is 20% short" is not.

    Both demand months merge into the 2026-10 bucket (200 t of capacity
    against 250 t of demand), and `current_week` is the bucket's own first
    week, so the 50 t overflow has nowhere earlier to go and becomes a real
    shortfall. Reverse the attribution order and the same 50 t lands on
    October instead -- which is the point of pinning the order."""
    lines = _run([DemandItem("A", "2026-10", Decimal("100")),
                  DemandItem("A", "2026-11", Decimal("150"))],
                 now=date(2026, 9, 28))
    assert _total(lines) == Decimal("250")
    october = [l for l in lines if l.demand_month == "2026-10"]
    assert not any(l.capacity_gap for l in october)
    assert _total(october) == Decimal("100")
    gaps = [l for l in lines if l.capacity_gap]
    assert [(l.demand_month, Decimal(str(l.qty))) for l in gaps] == [
        ("2026-11", Decimal("50"))]


def test_the_earliest_bucket_has_nowhere_earlier_to_go():
    """A bucket gap is provisional everywhere except at the start of the
    horizon: pre-build only overflows BACKWARDS, so in the first bucket it
    is already the final shortfall. It is a capacity shortfall, not a
    shelf-life one."""
    lines = _run([DemandItem("A", "2026-08", Decimal("100"))], lead=0,
                 now=date(2026, 8, 24))
    assert _total(lines) == Decimal("100")
    assert all(l.plan_week_start == date(2026, 8, 24) for l in lines)
    gap = [l for l in lines if l.capacity_gap]
    assert len(gap) == 1 and Decimal(str(gap[0].qty)) == Decimal("60")
    assert gap[0].shelf_life_ok is True and gap[0].is_prebuild is False
    assert "current week" in gap[0].prebuild_reason


def test_an_idle_week_in_the_same_bucket_is_used_before_reporting_a_shortfall():
    """`pack_bucket` may deliberately leave an open week idle beside a gap
    (its objective is unmet demand, not week occupancy -- Task 4 §修1/round
    3). That idle week is still real capacity, and a planner who can see it
    will not trust a plan that calls the same month short. So the backward
    walk starts at the LAST week of the bucket, not outside it."""
    caps = {date(2026, 8, 31): "20", date(2026, 9, 7): "5",
            date(2026, 9, 14): "40", date(2026, 9, 21): "30"}
    limits = lambda w: CapacityLimits(None, Decimal(caps.get(w, "0")), Decimal("1"))
    canvas = weeks_of_month("2026-09", _WEEKLY)

    packed = pack_bucket([BucketItem("A", "2026-09", Decimal("35")),
                          BucketItem("B", "2026-09", Decimal("40"))],
                         canvas, [CapacityLimits(None, Decimal(caps[w]), Decimal("1"))
                                  for w in canvas])
    assert any(l.capacity_gap for l in packed)                    # packer gives up
    assert date(2026, 9, 7) not in {l.plan_week_start for l in packed}   # ...idle

    lines = _run([DemandItem("A", "2026-09", Decimal("35")),
                  DemandItem("B", "2026-09", Decimal("40"))],
                 limits=limits, lead=0, now=date(2026, 1, 5))
    assert not any(l.capacity_gap for l in lines), [(str(l.plan_week_start), l.qty) for l in lines]
    assert _total(lines) == Decimal("75")
    rescued = [l for l in lines if l.plan_week_start == date(2026, 9, 7)]
    assert [Decimal(str(l.qty)) for l in rescued] == [Decimal("5")]

    # The rescued line is the walk placing INSIDE the bucket, two weeks
    # earlier than the (lead=0) target of 2026-09-21. It records that
    # distance and is still not a pre-build: it never left its own month.
    assert rescued[0].weeks_early == 2
    assert rescued[0].is_prebuild is False
    assert rescued[0].plan_week_month == "2026-09"
    assert all(not l.is_prebuild for l in lines)


def test_a_closed_week_is_stepped_over_by_the_backward_walk():
    """A shutdown week is skipped, not stopped at: the walk is a pre-build
    search (the month engine hops over full months the same way), not one of
    `pack_bucket`'s contiguous runs."""
    shut = date(2026, 10, 12)
    limits = lambda w: CapacityLimits(
        None, Decimal("0") if w == shut else Decimal("40"), Decimal("20"))
    lines = _run([DemandItem("A", "2026-10", Decimal("150"))], limits=limits,
                 lead=0, now=date(2026, 6, 1))
    assert _total(lines) == Decimal("150")
    assert not any(l.capacity_gap for l in lines)
    assert shut not in {l.plan_week_start for l in lines}
    assert date(2026, 10, 5) in {l.plan_week_start for l in lines}   # walked past it


def test_month_fixed_mode_walks_the_real_week_grid():
    """Weeks are not 7 days long under `month_fixed`, so the backward walk
    goes through `shift_weeks`, never `date - timedelta(weeks=1)`."""
    lines = _run([DemandItem("A", "2026-10", Decimal("300"))], lead=0,
                 now=date(2026, 6, 1), mode="month_fixed")
    assert _total(lines) == Decimal("300")
    assert not any(l.capacity_gap for l in lines)
    for l in lines:
        assert l.plan_week_start.day in (1, 8, 15, 22, 29), l
        assert l.plan_week_month == owning_month(l.plan_week_start, "month_fixed")
    # 2026-09-29 -> 2026-10-01 is a 2-day step; a timedelta walk would have
    # produced 2026-09-24, which is not on the grid at all.
    assert {date(2026, 9, 29), date(2026, 10, 1)} <= {l.plan_week_start for l in lines}


def test_two_buckets_never_double_book_a_week():
    """Every bucket is packed before any overflow runs, so a bucket's own
    demand outranks another bucket's pre-build in its weeks; overflow then
    resolves in ascending bucket order, so earlier demand claims the earlier
    weeks first."""
    lines = _run([DemandItem("A", "2026-10", Decimal("120")),
                  DemandItem("B", "2026-11", Decimal("300"))], lead=0,
                 now=date(2026, 6, 1))
    assert _total(lines) == Decimal("420")
    per_week: dict = {}
    for l in lines:
        if not l.capacity_gap:
            per_week[l.plan_week_start] = per_week.get(
                l.plan_week_start, Decimal("0")) + Decimal(str(l.qty))
    assert per_week and max(per_week.values()) <= Decimal("40")
    # The sharper form of the same claim: October's own plan is byte-identical
    # whether or not November exists, i.e. B's pre-build took only the room A
    # left and never displaced it.
    alone = _run([DemandItem("A", "2026-10", Decimal("120"))], lead=0,
                 now=date(2026, 6, 1))
    assert [(l.plan_week_start, l.qty) for l in alone] == [
        (l.plan_week_start, l.qty) for l in lines if l.material_code == "A"]


def test_nothing_is_lost_and_no_week_is_overfilled_across_the_pipeline():
    """Conservation and the two hard ceilings, over every shape this task
    changed: clamped leads, missing shelf life, shutdowns, multi-material
    buckets, both non-default week modes."""
    scenarios = [
        ([DemandItem("A", "2026-10", Decimal("300"))], {"A": 24}, 0, date(2026, 6, 1)),
        ([DemandItem("A", "2026-10", Decimal("300"))], {}, 0, date(2026, 1, 5)),
        ([DemandItem("A", "2026-08", Decimal("500"))], {"A": 24}, 12, date(2026, 8, 24)),
        ([DemandItem("A", "2026-10", Decimal("140")),
          DemandItem("B", "2026-10", Decimal("95")),
          DemandItem("A", "2026-11", Decimal("77"))], {"A": 24, "B": 6}, 4, date(2026, 7, 6)),
        ([DemandItem("A", "2026-10", Decimal("400"))], {"A": 3}, 0, date(2026, 1, 5)),
    ]
    for mode in ("iso_thursday", "iso_first_day", "month_fixed"):
        for demands, shelf, lead, now in scenarios:
            lines = _run(demands, limits=_cap("40", "20", 2), shelf=shelf,
                         lead=lead, now=now, mode=mode)
            assert _total(lines) == _total(demands), (mode, lead, shelf)
            per_week: dict = {}
            for l in lines:
                assert l.plan_week_month == owning_month(l.plan_week_start, mode)
                if l.capacity_gap:
                    continue
                assert l.plan_week_start >= week_start_of(now, mode)
                per_week.setdefault(l.plan_week_start, []).append(l)
            for week, rows in per_week.items():
                assert sum(Decimal(str(r.qty)) for r in rows) <= Decimal("40"), (mode, week)
                assert len({r.material_code for r in rows}) <= 2, (mode, week)


def test_lead_shortfall_marks_every_line_of_that_demand_month():
    """The clamp is a property of the demand month, not of one line."""
    lines = _run([DemandItem("A", "2026-08", Decimal("300")),
                  DemandItem("B", "2026-12", Decimal("30"))],
                 lead=12, now=date(2026, 8, 24))
    august = [l for l in lines if l.demand_month == "2026-08"]
    assert august and all(l.lead_shortfall for l in august)
    december = [l for l in lines if l.demand_month == "2026-12"]
    assert december and not any(l.lead_shortfall for l in december)
    assert all(l.plan_week_start >= date(2026, 8, 24) for l in lines)


def test_plan_week_month_is_the_owning_month_not_the_demand_month():
    """A week that straddles a month boundary belongs to whichever month the
    calendar module says -- 2026-09-28 is an OCTOBER week under
    `iso_thursday`, and the plan must say so even though it is a September
    date."""
    lines = _run([DemandItem("A", "2026-10", Decimal("30"))], lead=4)
    assert [(l.plan_week_start, l.plan_week_month) for l in lines] == [
        (date(2026, 9, 28), "2026-10")]


def test_non_positive_demand_is_dropped_and_no_demand_returns_nothing():
    assert _run([]) == []
    assert _run([DemandItem("A", "2026-10", Decimal("0")),
                 DemandItem("B", "2026-10", Decimal("-5"))]) == []
    kept = _run([DemandItem("A", "2026-10", Decimal("0")),
                 DemandItem("B", "2026-10", Decimal("30"))])
    assert {l.material_code for l in kept} == {"B"}


# ── Fix round 1 ─────────────────────────────────────────────────────────────


def test_the_in_bucket_shelf_life_gate_moves_a_slice_it_does_not_short_it():
    """A slice the packer put earlier than shelf life allows is handed BACK
    to the backward walk, never emitted as a shortfall on the spot.

    The walk starts at the bucket's last week, so the slice is re-offered
    every week the bucket has left -- **its own target week included, where
    it is not early at all and the gate does not apply**. Emitting the gap
    directly reported 20 t short against a completely EMPTY 2026-10-26 in a
    bucket holding 200 t of capacity against 80 t of demand, and a planner
    escalates against a false shortfall.

    Shelf life 1 month at a 1/2 margin allows 15 days before 2026-11-01;
    2026-10-12 is 20 days out (refused), 2026-10-19 is 13 (allowed)."""
    demands = [DemandItem("A", "2026-10", Decimal("40")),
               DemandItem("A", "2026-11", Decimal("40"))]
    lines = _run(demands, shelf={"A": 1}, margin="0.5")

    assert _total(lines) == Decimal("80")
    assert not any(l.capacity_gap for l in lines), [
        (str(l.plan_week_start), l.demand_month, str(l.qty), l.capacity_gap)
        for l in lines]
    november = [l for l in lines if l.demand_month == "2026-11"]
    assert _total(november) == Decimal("40")
    # ...and the gate really did bite: nothing for November sits earlier than
    # 15 days before it.
    for l in november:
        assert (date(2026, 11, 1) - l.plan_week_start).days <= 15, l
    # The rescued slice landed on its own target week, which was empty.
    assert date(2026, 10, 26) in {l.plan_week_start for l in november}


def test_a_slice_the_gate_refuses_is_still_gated_after_it_is_re_placed():
    """The rescue must not become an escape hatch: when the bucket has no
    late week left either, the quantity still ends as an explicit shortfall
    marked `shelf_life_ok=False`, not as production in a week shelf life
    forbids."""
    lines = _run([DemandItem("A", "2026-10", Decimal("200")),
                  DemandItem("A", "2026-11", Decimal("200"))],
                 shelf={"A": 1}, margin="0.5", now=date(2026, 9, 28))
    assert _total(lines) == Decimal("400")
    for l in lines:
        if l.capacity_gap:
            continue
        limit = 15 if l.demand_month == "2026-11" else 15
        anchor = date(2026, 11, 1) if l.demand_month == "2026-11" else date(2026, 10, 1)
        assert (anchor - l.plan_week_start).days <= limit, l
    blocked = [l for l in lines if l.capacity_gap and not l.shelf_life_ok]
    assert blocked, [(str(l.plan_week_start), l.demand_month, str(l.qty),
                      l.shelf_life_ok) for l in lines if l.capacity_gap]


def test_two_rows_for_one_material_in_one_week_are_folded_into_one_run():
    """`_merge_same_slot` is a live path, not decoration: the backward walk
    routinely lands in a week that already holds the same material for the
    same demand month (444 of 6,000 randomised pipelines produce a real+real
    pair). Two rows read as two production runs; physically it is one."""
    limits = _cap("120", "20", 1)
    demands = [DemandItem("A", "2026-09", Decimal("94")),
               DemandItem("A", "2026-10", Decimal("262"))]
    kwargs = dict(limits=limits, shelf={"A": None}, lead=6,
                  now=date(2026, 8, 27), mode="iso_first_day")

    lines = _run(demands, **kwargs)
    slots = [(l.material_code, l.demand_month, l.plan_week_start, l.capacity_gap)
             for l in lines]
    assert len(slots) == len(set(slots)), slots
    assert _total(lines) == Decimal("356")
    assert (date(2026, 9, 14), Decimal("76.5")) in {
        (l.plan_week_start, Decimal(str(l.qty))) for l in lines}

    # Without the fold the same plan carries two rows for 2026-09-14 and two
    # for 2026-09-21 -- same quantities, twice the apparent changeovers.
    import app.services.mps_engine as engine
    keep = engine._merge_same_slot
    try:
        engine._merge_same_slot = lambda rows: rows
        unfolded = _run(demands, **kwargs)
    finally:
        engine._merge_same_slot = keep
    assert len(unfolded) == len(lines) + 2
    assert _total(unfolded) == _total(lines)


def test_a_real_line_and_a_gap_line_in_one_week_are_never_folded():
    """The other half of the fold's contract. Folding them would hide a
    shortfall inside a production quantity -- the same week legitimately
    carries "produced 40" and "40 short" at once."""
    lines = _run([DemandItem("A", "2026-09", Decimal("292"))],
                 limits=_cap("40", "20", 1), shelf={"A": None}, margin="0.5",
                 lead=2, now=date(2026, 8, 3))
    assert _total(lines) == Decimal("292")
    shared = [l for l in lines if l.plan_week_start == date(2026, 9, 7)]
    assert sorted((l.capacity_gap, Decimal(str(l.qty))) for l in shared) == [
        (False, Decimal("40")), (True, Decimal("172"))]


# ── Locked lines ────────────────────────────────────────────────────────────


def _locked(code, demand_month, week, qty, **kw):
    return WeeklyLine(material_code=code, demand_month=demand_month,
                      plan_week_start=week,
                      plan_week_month=owning_month(week, _WEEKLY),
                      qty=Decimal(qty), locked=True, **kw)


def test_a_locked_line_is_echoed_and_only_the_remainder_is_replanned():
    """Locked production surviving a recalculate is shipped behaviour. The
    quantity comes off its own `(material, demand month)` demand BY WEEK, so
    a month that is part locked and part open keeps its open remainder --
    dropping the whole key (which is sound monthly) would silently delete
    it."""
    held = _locked("A", "2026-10", date(2026, 10, 5), "30")
    lines = _run([DemandItem("A", "2026-10", Decimal("100"))], locked=[held])
    assert held in lines                                  # echoed byte for byte
    assert _total(lines) == Decimal("100")                # 30 locked + 70 replanned
    replanned = [l for l in lines if not l.locked]
    assert _total(replanned) == Decimal("70")
    assert not any(l.capacity_gap for l in lines)


def test_locked_production_books_capacity_so_it_is_not_double_planned():
    """One 40 t week, 40 t already locked in it: there is no room left, and
    the rest must go elsewhere rather than being planned on top."""
    only = date(2026, 10, 26)
    limits = lambda w: CapacityLimits(None, Decimal("40") if w == only else Decimal("0"),
                                      Decimal("20"))
    held = _locked("A", "2026-10", only, "40")
    lines = _run([DemandItem("A", "2026-10", Decimal("55"))], limits=limits,
                 lead=0, locked=[held])
    assert _total(lines) == Decimal("55")
    assert sum(Decimal(str(l.qty)) for l in lines
               if l.plan_week_start == only and not l.capacity_gap) == Decimal("40")
    assert _total([l for l in lines if l.capacity_gap]) == Decimal("15")


def test_a_product_joins_its_own_locked_week_without_a_second_sku_slot():
    """Why the engine takes a pre-seeded LOAD rather than the caller
    shrinking `max_sku_count` from outside.

    `_WeekLoad.sku_room` deliberately does not charge a second slot for a
    material already in that week. Expressed from outside as
    `max_sku_count - 1`, a one-SKU week holding locked A becomes a zero-SKU
    week -- closed to A itself -- and the rest of A is spuriously short.
    With the load pre-seeded, A joins its own week and the week fills."""
    only = date(2026, 10, 26)
    limits = lambda w: CapacityLimits(1, Decimal("40") if w == only else Decimal("0"),
                                      Decimal("20"))
    held = _locked("A", "2026-10", only, "10")
    lines = _run([DemandItem("A", "2026-10", Decimal("50"))], limits=limits,
                 lead=0, locked=[held])
    assert _total(lines) == Decimal("50")
    assert sum(Decimal(str(l.qty)) for l in lines
               if l.plan_week_start == only and not l.capacity_gap) == Decimal("40")

    # The outside-in emulation, for contrast: max_sku_count 1 - 1 = 0 closes
    # the week outright and nothing can be produced there at all.
    assert all(l.capacity_gap for l in pack_bucket(
        [BucketItem("A", "2026-10", Decimal("40"))], [only],
        CapacityLimits(0, Decimal("30"), Decimal("20"))))


def test_a_run_flows_through_a_locked_week_instead_of_splitting_in_two():
    """P1 again: a locked week of the same product must not cut a run in
    half. Seeded into the ledger it is just an occupied week the run can
    keep filling; emulated as a closed or SKU-exhausted week it would be
    stepped over, stranding its remaining capacity and costing a changeover
    the month-based engine never charged."""
    middle = date(2026, 10, 12)
    held = _locked("A", "2026-10", middle, "10")
    canvas = weeks_of_month("2026-10", _WEEKLY)
    lines = _run([DemandItem("A", "2026-10", Decimal("140"))],
                 limits=_cap("40", "20", 1), lead=0, locked=[held])
    assert _total(lines) == Decimal("140")               # 130 replanned + 10 locked
    assert not any(l.capacity_gap for l in lines)
    used = sorted({canvas.index(l.plan_week_start) for l in lines})
    assert used == list(range(used[0], used[-1] + 1)), used     # one unbroken run
    assert sum(Decimal(str(l.qty)) for l in lines
               if l.plan_week_start == middle) == Decimal("40")  # locked week filled


def test_locked_quantity_beyond_the_demand_is_kept_not_overruled():
    """A planner may have locked more than the current forecast asks for.
    The engine echoes it and charges its capacity; it does not silently
    delete a committed batch because a forecast moved."""
    held = _locked("A", "2026-10", date(2026, 10, 5), "120")
    lines = _run([DemandItem("A", "2026-10", Decimal("50"))], locked=[held])
    assert lines == [held]


def test_only_locked_input_still_returns_the_locked_plan():
    held = _locked("A", "2026-10", date(2026, 10, 5), "30")
    assert _run([], locked=[held]) == [held]
    assert _run([DemandItem("A", "2026-10", Decimal("0"))], locked=[held]) == [held]


def test_two_locked_lines_on_one_slot_fold_even_when_nothing_is_left_to_plan():
    """The "nothing left to plan" early return owes the same fold as the
    long path -- this is what AdjustDrawer's "Merge into adjacent week"
    depends on.

    That handler locks BOTH lines onto one week and tells the planner to
    hit Recalculate; the fold is `_merge_same_slot`'s job at the bottom of
    `generate_mps`. The early return for "every demand is already covered
    by locked lines" used to skip it, so the acceptance case -- ONE product
    whose whole demand is those two locked lines -- came back as two 20 t
    rows, while the very same pair folded to one 40 t row as soon as any
    unrelated open demand kept the function off the early path.

    The second half is the point: both paths must agree. A fix that folds
    only when something else happens to be open is not a fix."""
    week = date(2026, 10, 5)
    pair = [_locked("A", "2026-10", week, "20000"),
            _locked("A", "2026-10", week, "20000")]
    roomy = _cap("100000", None, None)

    # Nothing open: the early return path (payload is empty).
    only_locked = _run([DemandItem("A", "2026-10", Decimal("40000"))],
                       limits=roomy, locked=pair)
    assert [(l.plan_week_start, Decimal(str(l.qty)), l.locked) for l in only_locked] == [
        (week, Decimal("40000"), True)]

    # One unrelated open demand: the long path. Same product, same fold.
    with_open = _run([DemandItem("A", "2026-10", Decimal("40000")),
                      DemandItem("B", "2026-10", Decimal("5000"))],
                     limits=roomy, locked=pair)
    assert [(l.plan_week_start, Decimal(str(l.qty)), l.locked)
            for l in with_open if l.material_code == "A"] == [
        (week, Decimal("40000"), True)]
    assert _total(with_open) == Decimal("45000")


def test_a_preload_of_the_wrong_length_is_refused():
    """Same contract as the per-week limits sequence: booking a locked batch
    into the wrong week is worse than refusing to plan."""
    import pytest
    with pytest.raises(ValueError, match="preload must line up"):
        pack_bucket([BucketItem("A", "2026-08", Decimal("10"))], WEEKS,
                    CapacityLimits(None, Decimal("40"), Decimal("20")),
                    preloaded=[{}, {}, {}])


# ── Fix round 2 ─────────────────────────────────────────────────────────────


def test_levelling_inside_the_bucket_is_not_a_prebuild_but_crossing_one_is():
    """`is_prebuild` and `weeks_early` answer different questions.

    Under a whole-month canvas, producing early WITHIN the demand's own
    bucket month is ordinary levelling -- the very thing the whole-month
    canvas exists to enable -- not production pulled ahead of need. Flagging
    it made 87% of the lines of a gap-free plan read "pre-built" at
    `lead_weeks=0` on a plan with zero cross-bucket movement, which is
    wallpaper rather than a warning.

    So `is_prebuild` is true only when the plan week's OWNING MONTH precedes
    the demand's bucket month, while `weeks_early` stays the plain week
    distance from the target -- the quantity the shelf-life gate reasons
    about. A levelled line therefore reads `weeks_early > 0` together with
    `is_prebuild=False`, and that pairing is correct.

    300 t against a 200 t October bucket: five weeks fill October, the
    remaining 100 t crosses into September."""
    lines = _run([DemandItem("A", "2026-10", Decimal("300"))], lead=0,
                 now=date(2026, 6, 1))
    assert _total(lines) == Decimal("300")
    assert not any(l.capacity_gap for l in lines)

    for l in lines:
        assert l.is_prebuild == (l.plan_week_month < "2026-10"), l

    inside = [l for l in lines if l.plan_week_month == "2026-10"]
    crossed = [l for l in lines if l.plan_week_month == "2026-09"]
    assert inside and crossed

    # Half one: levelled inside the bucket -- early in weeks, not a pre-build,
    # and carrying no reason, because nothing happened worth explaining.
    assert [l for l in inside if l.weeks_early > 0], "no levelled line to test"
    for l in inside:
        assert l.is_prebuild is False and l.prebuild_reason is None, l
    assert max(l.weeks_early for l in inside) == 4

    # Half two: pulled into an earlier bucket month -- flagged, with a reason.
    for l in crossed:
        assert l.is_prebuild is True and l.weeks_early > 0, l
        assert "pre-built" in l.prebuild_reason

    # ...and the test is the OWNING month, not the calendar date: 2026-09-28
    # is a September date belonging to an October week, and is not a pre-build.
    sep28 = [l for l in lines if l.plan_week_start == date(2026, 9, 28)]
    assert len(sep28) == 1
    assert sep28[0].plan_week_month == "2026-10"
    assert sep28[0].is_prebuild is False and sep28[0].weeks_early == 4


def test_a_gap_free_plan_flags_no_prebuild_when_nothing_crossed_a_bucket():
    """The property behind the number: on a plan that fits, no line claims to
    be a pre-build, at any lead. This is what 87% used to look like."""
    codes = [f"P{i}" for i in range(4)]
    demands = [DemandItem(c, m, Decimal("100"))
               for c in codes for m in ("2026-09", "2026-10", "2026-11")]
    limits = _cap("400", "20")
    for lead in (0, 1, 2, 4, 6):
        lines = _run(demands, limits=limits, shelf={c: 24 for c in codes},
                     lead=lead, now=date(2026, 6, 1))
        assert not any(l.capacity_gap for l in lines), lead
        assert _total(lines) == Decimal("1200"), lead
        flagged = [l for l in lines if l.is_prebuild]
        assert not flagged, (lead, [(str(l.plan_week_start), l.plan_week_month,
                                     l.demand_month) for l in flagged])
        # ...while the week distances are still recorded.
        assert any(l.weeks_early > 0 for l in lines), lead


# ── Fix round 3 ─────────────────────────────────────────────────────────────


def test_a_locked_shortfall_line_is_dropped_not_echoed():
    """A locked line carrying `capacity_gap=True` is discarded on the way in.

    Callers really do produce them -- the API layer rebuilds locked lines
    from whatever the planner locked, `capacity_gap` flag and all. A
    shortfall is not committed production, so it is already skipped when
    seeding the ledger and when subtracting from demand; echoing it anyway
    emitted the stale gap AND re-planned the same demand in full, so 100 t
    of demand plus a 40 t locked gap came out as a 140 t plan.

    Dropping is right and subtracting would be actively wrong: subtracting
    turns "we could not make this" into "we no longer need this" and deletes
    real demand for good. A gap is derived data -- this run recomputes it
    from current demand and current capacity -- so a stale one is discarded
    and the shortage has to prove itself again."""
    stale = _locked("A", "2026-10", date(2026, 10, 5), "40", capacity_gap=True)
    lines = _run([DemandItem("A", "2026-10", Decimal("100"))], locked=[stale])

    assert _total(lines) == Decimal("100")          # not 140
    assert stale not in lines
    assert not any(l.locked for l in lines)
    assert not any(l.capacity_gap for l in lines)   # capacity was ample; re-proved

    # A real locked line alongside a stale gap: the real one still counts.
    real = _locked("A", "2026-10", date(2026, 10, 5), "30")
    mixed = _run([DemandItem("A", "2026-10", Decimal("100"))], locked=[real, stale])
    assert _total(mixed) == Decimal("100")
    assert real in mixed and stale not in mixed
    assert _total([l for l in mixed if not l.locked]) == Decimal("70")


def test_a_locked_shortfall_does_not_suppress_a_shortage_that_is_still_real():
    """Discarding the stale gap must not lose the shortage -- if the demand
    still does not fit, this run says so on its own evidence."""
    only = date(2026, 10, 26)
    limits = lambda w: CapacityLimits(None, Decimal("40") if w == only else Decimal("0"),
                                      Decimal("20"))
    stale = _locked("A", "2026-10", only, "60", capacity_gap=True)
    lines = _run([DemandItem("A", "2026-10", Decimal("100"))], limits=limits,
                 lead=0, locked=[stale])
    assert _total(lines) == Decimal("100")
    assert _total([l for l in lines if l.capacity_gap]) == Decimal("60")
    assert not any(l.locked for l in lines)
