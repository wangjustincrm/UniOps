"""MPS scheduling algorithm (Phase 1B Task 3).

Pure-function tests only -- no DB, no clock. Cases 1-4 are verbatim from the
task brief; the rest cover edge cases the algorithm text implies (SKU-count
overflow, locked-line capacity consumption, prebuild_reason content, a
multi-hop cascade that must skip full intermediate months, and the
distinction between a shelf-life-caused gap vs a pure-capacity-caused gap).
"""
from decimal import Decimal

from app.services.mps_engine import (
    CapacityLimits, DemandItem, PlannedLine, generate_mps,
)


# ── Brief's verbatim cases ───────────────────────────────────────────────────


def test_under_capacity_places_in_demand_month():
    out = generate_mps(
        [DemandItem("S0060", "2026-11", Decimal("100"))],
        CapacityLimits(max_sku_count=12, max_output_qty=Decimal("160000")),
        {"S0060": 18}, Decimal("0.3333"),
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
        {"A": 18, "B": 18}, Decimal("0.3333"),
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
        {"A": 1, "B": 1}, Decimal("0.3333"),
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
        {"A": None, "B": None}, Decimal("0.3333"),
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
        {"A": 18, "B": 18}, Decimal("0.3333"),
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
        {"A": 18}, Decimal("0.3333"), locked=locked,
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
        shelf_life, Decimal("0.3333"),
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
        shelf_life, Decimal("0.3333"), locked=locked,
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
        {"P": 18}, Decimal("0.3333"),
    )
    assert len(out) == 2
    by_month = {l.demand_month: l for l in out}
    assert by_month["2026-11"].plan_month == "2026-11"
    assert by_month["2026-12"].plan_month == "2026-12"
    assert not any(l.is_prebuild for l in out)
