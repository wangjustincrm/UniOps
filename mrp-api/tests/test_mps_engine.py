"""MPS scheduling algorithm (Phase 1B Task 3 + production-lead-time task 1).

Pure-function tests only -- no DB, no clock. Cases 1-4 in the first section
are verbatim from the original task brief; the rest cover edge cases the
algorithm text implies (SKU-count overflow, locked-line capacity consumption,
prebuild_reason content, a multi-hop cascade that must skip full intermediate
months, and the distinction between a shelf-life-caused gap vs a
pure-capacity-caused gap). The lead-time section below is verbatim from the
2026-08-07 production-lead-time task brief.

All calls pass `lead_months` and `current_month` explicitly (no defaults on
`generate_mps`); pre-lead tests pass `lead_months=0` and a `current_month`
safely before every demand month in the test, which the engine guarantees
reproduces the exact pre-lead placement/prebuild/gap behaviour byte-for-byte
(see `test_lead_zero_reproduces_same_month` below for the dedicated
regression case).
"""
from datetime import date
from decimal import Decimal

from app.services.mps_engine import (
    BucketItem, CapacityLimits, DemandItem, PlannedLine, generate_mps, pack_bucket,
)

# Anchor used by pre-lead tests below: always <= every demand month they use,
# so lead_months=0 reproduces the exact pre-lead behaviour (no clamping).
_NO_LEAD_ANCHOR = "2020-01"


# ── Brief's verbatim cases ───────────────────────────────────────────────────


def test_under_capacity_places_in_demand_month():
    out = generate_mps(
        [DemandItem("S0060", "2026-11", Decimal("100"))],
        CapacityLimits(max_sku_count=12, max_output_qty=Decimal("160000")),
        {"S0060": 18}, Decimal("0.3333"), lead_months=0, current_month=_NO_LEAD_ANCHOR,
    )
    assert len(out) == 1
    line = out[0]
    assert (line.plan_month, line.is_prebuild, line.capacity_gap) == ("2026-11", False, False)
    assert line.prebuild_reason is None
    assert line.shelf_life_ok is True
    assert line.locked is False


def test_output_overflow_prebuilds_to_prior_month():
    demands = [
        DemandItem("A", "2026-11", Decimal("120000")),
        DemandItem("B", "2026-11", Decimal("120000")),
    ]
    out = generate_mps(
        demands, CapacityLimits(max_sku_count=12, max_output_qty=Decimal("160000")),
        {"A": 18, "B": 18}, Decimal("0.3333"), lead_months=0, current_month=_NO_LEAD_ANCHOR,
    )
    by_code = {l.material_code: l for l in out}
    # 240k demanded in one month, 160k/mo cap -> one product pre-built earlier
    assert any(l.is_prebuild and l.plan_month == "2026-10" for l in out)
    assert sum(l.qty for l in out) == Decimal("240000")
    assert not any(l.capacity_gap for l in out)
    prebuilt = [l for l in out if l.is_prebuild][0]
    assert prebuilt.prebuild_reason == "2026-11 over max_output_qty 160000 KG"
    assert prebuilt.demand_month == "2026-11"
    assert by_code[prebuilt.material_code].shelf_life_ok is True


def test_shelf_life_blocks_too_early_prebuild_becomes_gap():
    # 3 months of full demand, 1-month shelf life (minus safety) forbids
    # pre-building more than ~0 months ahead -> the overflow can't move.
    demands = [DemandItem(c, "2026-11", Decimal("160000")) for c in ("A", "B")]
    out = generate_mps(
        demands, CapacityLimits(max_sku_count=12, max_output_qty=Decimal("160000")),
        {"A": 1, "B": 1}, Decimal("0.3333"), lead_months=0, current_month=_NO_LEAD_ANCHOR,
    )
    assert any(l.capacity_gap for l in out)
    gap = [l for l in out if l.capacity_gap][0]
    # never silently drop: the gap line still carries the full demand qty
    # and stays pinned to its own demand month.
    assert gap.plan_month == gap.demand_month == "2026-11"
    assert gap.qty == Decimal("160000")
    assert gap.shelf_life_ok is False
    assert sum(l.qty for l in out) == Decimal("320000")


def test_unknown_shelf_life_is_never_prebuilt():
    demands = [DemandItem(c, "2026-11", Decimal("160000")) for c in ("A", "B")]
    out = generate_mps(
        demands, CapacityLimits(max_sku_count=12, max_output_qty=Decimal("160000")),
        {"A": None, "B": None}, Decimal("0.3333"), lead_months=0, current_month=_NO_LEAD_ANCHOR,
    )
    assert all(not l.is_prebuild for l in out)
    assert any(l.capacity_gap for l in out)
    gap = [l for l in out if l.capacity_gap][0]
    assert gap.shelf_life_ok is False


# ── Additional edge cases ────────────────────────────────────────────────────


def test_sku_count_overflow_prebuilds_to_prior_month():
    # Qty is well under the (unlimited) output cap; only the SKU-count cap
    # is breached, so the mechanism forcing a pre-build must be the SKU
    # check, not the qty check.
    demands = [
        DemandItem("A", "2026-11", Decimal("50")),
        DemandItem("B", "2026-11", Decimal("50")),
    ]
    out = generate_mps(
        demands, CapacityLimits(max_sku_count=1, max_output_qty=None),
        {"A": 18, "B": 18}, Decimal("0.3333"), lead_months=0, current_month=_NO_LEAD_ANCHOR,
    )
    assert not any(l.capacity_gap for l in out)
    prebuilt = [l for l in out if l.is_prebuild]
    assert len(prebuilt) == 1
    assert prebuilt[0].plan_month == "2026-10"
    assert prebuilt[0].prebuild_reason == "2026-11 over max_sku_count 1"


def test_locked_line_consumes_capacity_forces_sibling_prebuild():
    locked = [
        PlannedLine(
            material_code="Z", demand_month="2026-11", plan_month="2026-11",
            qty=Decimal("150000"), is_prebuild=False, prebuild_reason=None,
            shelf_life_ok=True, capacity_gap=False, locked=True,
        )
    ]
    demands = [DemandItem("A", "2026-11", Decimal("50000"))]
    out = generate_mps(
        demands, CapacityLimits(max_sku_count=12, max_output_qty=Decimal("160000")),
        {"A": 18}, Decimal("0.3333"), lead_months=0, current_month=_NO_LEAD_ANCHOR, locked=locked,
    )
    # the locked line passes through completely untouched...
    locked_out = [l for l in out if l.locked]
    assert locked_out == locked
    # ...and its 150k already consumes almost all of 2026-11's 160k cap, so
    # A (50k) cannot fit alongside it and must pre-build to 2026-10.
    a_line = [l for l in out if l.material_code == "A"][0]
    assert a_line.is_prebuild is True
    assert a_line.plan_month == "2026-10"
    assert a_line.capacity_gap is False


def test_multi_month_cascade_skips_full_earlier_months_to_find_room():
    # 2026-09 and 2026-10 are each pre-filled to exactly capacity by their
    # own demand; when 2026-11 overflows, the nearest-earlier-month search
    # must hop over both full months and land in 2026-08 (hop=3).
    demands = [
        DemandItem("F", "2026-09", Decimal("100")),
        DemandItem("G", "2026-10", Decimal("100")),
        DemandItem("H", "2026-11", Decimal("100")),
        DemandItem("I", "2026-11", Decimal("50")),
    ]
    shelf_life = {"F": 18, "G": 18, "H": 3, "I": 6}
    out = generate_mps(
        demands, CapacityLimits(max_sku_count=None, max_output_qty=Decimal("100")),
        shelf_life, Decimal("0.3333"), lead_months=0, current_month=_NO_LEAD_ANCHOR,
    )
    assert not any(l.capacity_gap for l in out)
    i_line = [l for l in out if l.material_code == "I"][0]
    assert i_line.is_prebuild is True
    assert i_line.plan_month == "2026-08"
    assert sum(l.qty for l in out) == Decimal("350")


def test_capacity_gap_from_pure_capacity_keeps_shelf_life_ok_true():
    # 2026-10 is filled solid by a locked line. Z has only a 1-hop shelf-life
    # budget (shelf_life=2, floor(2*0.6667)=1) so its only legal target is
    # 2026-10 -- which has no room. The block here is capacity, not shelf
    # life, so shelf_life_ok must stay True even though the line is a gap.
    locked = [
        PlannedLine(
            material_code="X", demand_month="2026-10", plan_month="2026-10",
            qty=Decimal("100"), is_prebuild=False, prebuild_reason=None,
            shelf_life_ok=True, capacity_gap=False, locked=True,
        )
    ]
    demands = [
        DemandItem("Y", "2026-11", Decimal("100")),
        DemandItem("Z", "2026-11", Decimal("50")),
    ]
    shelf_life = {"Y": 1, "Z": 2}
    out = generate_mps(
        demands, CapacityLimits(max_sku_count=None, max_output_qty=Decimal("100")),
        shelf_life, Decimal("0.3333"), lead_months=0, current_month=_NO_LEAD_ANCHOR, locked=locked,
    )
    z_line = [l for l in out if l.material_code == "Z"][0]
    assert z_line.capacity_gap is True
    assert z_line.plan_month == "2026-11"
    assert z_line.shelf_life_ok is True
    assert z_line.prebuild_reason == "2026-11 over max_output_qty 100 KG"


def test_demand_across_multiple_months_processed_ascending_independently():
    demands = [
        DemandItem("P", "2026-12", Decimal("10")),
        DemandItem("P", "2026-11", Decimal("10")),
    ]
    out = generate_mps(
        demands, CapacityLimits(max_sku_count=12, max_output_qty=Decimal("160000")),
        {"P": 18}, Decimal("0.3333"), lead_months=0, current_month=_NO_LEAD_ANCHOR,
    )
    assert len(out) == 2
    by_month = {l.demand_month: l for l in out}
    assert by_month["2026-11"].plan_month == "2026-11"
    assert by_month["2026-12"].plan_month == "2026-12"
    assert not any(l.is_prebuild for l in out)


# ── Production lead time (2026-08-07 task 1 brief, verbatim) ────────────────

UNL = CapacityLimits(max_sku_count=None, max_output_qty=None)


def test_lead_schedules_one_month_before_demand():
    out = generate_mps([DemandItem("A", "2026-11", Decimal("10"))], UNL,
                       {"A": 24}, Decimal("0"), lead_months=1, current_month="2026-08")
    assert len(out) == 1
    assert out[0].plan_month == "2026-10"           # D - lead, capacity was free
    assert out[0].is_prebuild is False and out[0].lead_shortfall is False


def test_lead_clamped_to_current_month_flags_shortfall():
    # demand next month, lead 1 -> target = this month's predecessor = past -> clamp to now
    out = generate_mps([DemandItem("A", "2026-08", Decimal("10"))], UNL,
                       {"A": 24}, Decimal("0"), lead_months=1, current_month="2026-08")
    assert out[0].plan_month == "2026-08"
    assert out[0].lead_shortfall is True


def test_capacity_full_at_target_prebuilds_earlier_down_to_current_floor():
    demands = [DemandItem(c, "2026-11", Decimal("100")) for c in ("A", "B")]
    out = generate_mps(demands, CapacityLimits(max_sku_count=12, max_output_qty=Decimal("100")),
                       {"A": 24, "B": 24}, Decimal("0"), lead_months=1, current_month="2026-08")
    # target 2026-10 holds one; the other pre-builds to 2026-09 (earlier than standard target)
    assert any(l.is_prebuild and l.plan_month == "2026-09" for l in out)
    assert not any(l.plan_month < "2026-08" for l in out)   # never before current


def test_lead_zero_reproduces_same_month():
    out = generate_mps([DemandItem("A", "2026-11", Decimal("10"))], UNL,
                       {"A": 24}, Decimal("0"), lead_months=0, current_month="2026-08")
    assert out[0].plan_month == "2026-11" and out[0].is_prebuild is False and out[0].lead_shortfall is False


def test_shelf_life_shorter_than_lead_is_a_gap():
    # lead 2, shelf life 1 (minus safety) can't cover producing 2 months early -> gap
    out = generate_mps([DemandItem("A", "2026-11", Decimal("10"))], UNL,
                       {"A": 1}, Decimal("0"), lead_months=2, current_month="2026-08")
    assert out[0].capacity_gap is True and out[0].shelf_life_ok is False


def test_lead_clamped_and_capacity_full_at_current_month_is_gap_with_shortfall():
    # T1-review carry-over: lead_shortfall=True and capacity_gap=True on the
    # SAME line. lead=3 pushes standard_target ("2026-07") before
    # current_month ("2026-08") -> clamp -> lead_shortfall=True. The item's
    # own 100 qty alone already exceeds the 50 output cap at the clamped
    # target, and there is no earlier month to search (current_month is the
    # floor) -> capacity_gap=True too. Ample shelf life (18mo, offset 2 <=
    # 12) means the gap is a pure capacity shortfall, not a shelf-life one.
    out = generate_mps(
        [DemandItem("A", "2026-10", Decimal("100"))],
        CapacityLimits(max_sku_count=1, max_output_qty=Decimal("50")),
        {"A": 18}, Decimal("0.3333"), lead_months=3, current_month="2026-08",
    )
    assert len(out) == 1
    line = out[0]
    assert line.plan_month == "2026-08"          # clamped to current_month
    assert line.lead_shortfall is True
    assert line.capacity_gap is True
    assert line.is_prebuild is False              # gap lines are never is_prebuild
    assert line.shelf_life_ok is True              # blocked by capacity, not shelf life


# ── Weekly bucket packing (weekly-MPS Task 4 brief, verbatim) ───────────────
#
# Single-bucket packing only: no lead time, no cross-bucket pre-build, no
# shelf-life gate (those stay with `generate_mps` above and are re-based onto
# weeks by Task 5). `pack_bucket` is a pure function that receives an
# already-resolved `list[date]` of week starts (it must never care which of
# `week_calendar.py`'s three modes produced them) and already-resolved
# `CapacityLimits`.

WEEKS = [date(2026, 8, 3), date(2026, 8, 10), date(2026, 8, 17), date(2026, 8, 24)]


def _items(**kv):
    return [BucketItem(code, "2026-08", Decimal(str(q))) for code, q in kv.items()]


def _by_week(lines):
    out = {}
    for l in lines:
        out.setdefault(l.plan_week_start, []).append((l.material_code, str(l.qty)))
    return {w: sorted(v) for w, v in out.items()}


def test_golden_case_from_the_business_owner():
    """A60 B20 C30 D30, cap 40/week, 4 weeks — the plan the planner drew by hand.

    W1 A40 | W2 A20+B20 | W3 C30 | W4 D30
    """
    limits = CapacityLimits(max_sku_count=None, max_output_qty=Decimal("40"),
                            min_output_qty=Decimal("20"))
    lines = pack_bucket(_items(A=60, B=20, C=30, D=30), WEEKS, limits)
    assert _by_week(lines) == {
        WEEKS[0]: [("A", "40")],
        WEEKS[1]: [("A", "20"), ("B", "20")],
        WEEKS[2]: [("C", "30")],
        WEEKS[3]: [("D", "30")],
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
            WEEKS[0]: [("A", "40")],
            WEEKS[1]: [("A", "20"), ("B", "20")],
            WEEKS[2]: [("B", "40")],
            WEEKS[3]: [("C", "30")],
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
