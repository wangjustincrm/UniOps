"""Why the plan says what it says — the derivation, not the number.

Asked why the plan makes 619 of S0102 in the second week of October, the
assistant said the system records the plan but not the reasoning. It was the
same mistake it once made about approvers: the reasoning is recorded, spread
across the line's own columns, and nothing had been wired up to assemble it.
"""
from datetime import date
from decimal import Decimal

from app.services import plan_view


def _row(**kw) -> dict:
    base = {
        "material_code": "S0102", "demand_month": "2026-10",
        "plan_week_start": date(2026, 10, 12), "qty": Decimal("619"),
        "demand_forecast": Decimal("4990"), "opening_stock": Decimal("4371"),
        "carry_in_qty": Decimal("0"), "surplus_qty": Decimal("0"),
        "weeks_early": 0, "prebuild_reason": None, "shelf_life_ok": True,
    }
    base.update({f: False for f in plan_view.FLAG_MEANINGS})
    base.update(kw)
    return base


def test_the_plain_case_walks_demand_to_quantity():
    out = plan_view.explain(_row())
    assert out["arithmetic_accounts_for_it"] is True
    assert out["unexplained"] is False
    values = [s["value"] for s in out["steps"]]
    # forecast, opening, net, planned — the subtraction a planner can follow.
    assert values == ["4990", "4371", "619", "619"]


def test_minimum_lot_surplus_is_part_of_the_arithmetic():
    """The bug this test exists for.

    A line of 14,910 against a net requirement of 7,020 with 7,890 of
    minimum-lot surplus adds up exactly — and the check left surplus out of the
    sum, declared the quantity underived, and the reply told a planner it might
    be a display fault. Saying "I cannot account for this" about a number that
    accounts for itself is its own wrong answer.
    """
    out = plan_view.explain(_row(
        qty=Decimal("14910"), demand_forecast=Decimal("7020"),
        opening_stock=Decimal("0"), surplus_qty=Decimal("7890")))
    assert out["arithmetic_accounts_for_it"] is True
    assert out["unexplained"] is False
    assert any("minimum lot" in s["label"] for s in out["steps"])


def test_a_zero_line_carries_the_reason_it_is_zero():
    """Zero against real demand reads as an omission unless something says
    otherwise. The engine keeps the line deliberately — with no line the cell
    reads "no demand" rather than "already made"."""
    out = plan_view.explain(_row(
        qty=Decimal("0"), demand_forecast=Decimal("5890"),
        opening_stock=Decimal("0"), carry_in_qty=Decimal("5890"),
        covered_by_carry=True))
    assert out["arithmetic_accounts_for_it"] is True
    flags = {r["flag"] for r in out["reasons"]}
    assert "covered_by_carry" in flags
    assert any("carried in" in s["label"].lower() for s in out["steps"])


def test_an_unaccounted_quantity_is_reported_as_unaccounted():
    """The honest failure. If the figures do not add up and no flag explains the
    difference, that gap IS the answer — inventing a plausible reason to close
    it is the one outcome this whole layer exists to prevent."""
    out = plan_view.explain(_row(qty=Decimal("9999")))
    assert out["arithmetic_accounts_for_it"] is False
    assert out["unexplained"] is True
    assert not out["reasons"]


def test_a_flag_that_fires_brings_the_engines_own_words():
    out = plan_view.explain(_row(capacity_gap=True))
    meanings = {r["flag"]: r["meaning"] for r in out["reasons"]}
    assert "capacity_gap" in meanings
    assert meanings["capacity_gap"] == plan_view.FLAG_MEANINGS["capacity_gap"]


def test_shelf_life_is_reported_when_it_is_NOT_ok():
    """Inverted: every other flag is news when true, this one when false. Read
    the same way as the others it would be silent in exactly the bad case."""
    out = plan_view.explain(_row(shelf_life_ok=False))
    assert "shelf_life_ok" in {r["flag"] for r in out["reasons"]}
    assert "shelf_life_ok" not in {
        r["flag"] for r in plan_view.explain(_row())["reasons"]}


def test_negative_net_requirement_does_not_go_below_zero():
    """More stock than demand is a net requirement of zero, not a negative one —
    mrp-api's own formula is max(0, forecast - opening)."""
    out = plan_view.explain(_row(
        qty=Decimal("0"), demand_forecast=Decimal("100"),
        opening_stock=Decimal("500")))
    assert "-" not in " ".join(s["value"] or "" for s in out["steps"])
