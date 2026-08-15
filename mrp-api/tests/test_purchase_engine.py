"""Phase 1C 的运算内核 —— 纯函数，不碰库。

链路：生效计划的产量 → 展开 BOM → 加损耗 → 得到「组件 × 需求周」毛需求
→ 扣库存（逐周滚动）→ 按供应商提前期倒推下单周 → 起订量取整 → 建议行。
"""
from datetime import date
from decimal import Decimal

import pytest

from app.services.purchase_engine import (
    ComponentDemand, SupplyParams, explode_demands, net_off_inventory, plan_orders,
)

W1, W2, W3 = date(2026, 9, 5), date(2026, 9, 12), date(2026, 9, 19)


# ── 展开 + 损耗 ──────────────────────────────────────────────────────────


def _bom(tree):
    """`{产品: [(组件, 单位用量), ...]}` 的查表器。★用量已是单位用量：
    NC 的批量除数在同步时就归一了，这里再除一次会差几个数量级。"""
    return lambda code: tree.get(code, [])


def test_a_products_requirement_becomes_its_components():
    lines = explode_demands(
        [("S0093", W1, Decimal("100"))],
        _bom({"S0093": [("CW0001", Decimal("1.0")), ("CP0115", Decimal("1.4524"))]}),
        rates={"raw": Decimal("0"), "packaging": Decimal("0")},
    )
    assert sorted((l.material_code, l.week, l.qty) for l in lines) == [
        ("CP0115", W1, Decimal("145.24")),
        ("CW0001", W1, Decimal("100.0")),
    ]


def test_each_component_takes_the_rate_for_its_own_category():
    """★逐层按组件自身类别取率，不累乘祖先层。包材 CP* 用包材率，其余用原料率。"""
    lines = explode_demands(
        [("S0093", W1, Decimal("100"))],
        _bom({"S0093": [("CR0001", Decimal("1")), ("CP0115", Decimal("2"))]}),
        rates={"raw": Decimal("0.02"), "packaging": Decimal("0.05")},
    )
    by_code = {l.material_code: l.qty for l in lines}
    assert by_code["CR0001"] == Decimal("102")     # 100 x 1.02
    assert by_code["CP0115"] == Decimal("210")     # 200 x 1.05


def test_multi_level_explosion_does_not_compound_ancestor_rates():
    """半成品粉 CS 用原料率；它下面的 CR 也用原料率 —— 一次，不是两次。"""
    lines = explode_demands(
        [("S0093", W1, Decimal("100"))],
        _bom({
            "S0093": [("CS0026", Decimal("1"))],
            "CS0026": [("CR0024", Decimal("0.27"))],
        }),
        rates={"raw": Decimal("0.10"), "packaging": Decimal("0")},
    )
    by_code = {l.material_code: l.qty for l in lines}
    assert by_code["CS0026"] == Decimal("110")           # 100 x 1.1
    # 若累乘祖先层会得到 110 x 0.27 x 1.1 = 32.67 —— 那是错的
    assert by_code["CR0024"] == Decimal("29.7")          # (100 x 0.27) x 1.1


def test_a_product_with_no_bom_is_reported_not_silently_dropped():
    lines = explode_demands(
        [("S0093", W1, Decimal("100")), ("NOBOM", W1, Decimal("50"))],
        _bom({"S0093": [("CR0001", Decimal("1"))]}),
        rates={"raw": Decimal("0"), "packaging": Decimal("0")},
    )
    missing = [l for l in lines if l.missing_bom]
    assert [l.material_code for l in missing] == ["NOBOM"]
    assert missing[0].qty == Decimal("0")


def test_the_same_component_from_two_products_is_summed_per_week():
    lines = explode_demands(
        [("A", W1, Decimal("10")), ("B", W1, Decimal("20"))],
        _bom({"A": [("CR0001", Decimal("1"))], "B": [("CR0001", Decimal("2"))]}),
        rates={"raw": Decimal("0"), "packaging": Decimal("0")},
    )
    assert [(l.material_code, l.qty) for l in lines if not l.missing_bom] == [
        ("CR0001", Decimal("50"))]


# ── 扣库存（逐周滚动）────────────────────────────────────────────────────


def test_stock_covers_the_earliest_weeks_first():
    """库存不是每周都能重来一遍 —— 先到先用，剩下的结转。"""
    net = net_off_inventory(
        [ComponentDemand("CR0001", W1, Decimal("30")),
         ComponentDemand("CR0001", W2, Decimal("30")),
         ComponentDemand("CR0001", W3, Decimal("30"))],
        on_hand={"CR0001": Decimal("50")},
    )
    assert [(l.week, l.gross, l.available, l.net) for l in net] == [
        (W1, Decimal("30"), Decimal("50"), Decimal("0")),
        (W2, Decimal("30"), Decimal("20"), Decimal("10")),
        (W3, Decimal("30"), Decimal("0"), Decimal("30")),
    ]


def test_stock_of_one_material_never_covers_another():
    net = net_off_inventory(
        [ComponentDemand("CR0001", W1, Decimal("30")),
         ComponentDemand("CR0002", W1, Decimal("30"))],
        on_hand={"CR0001": Decimal("100")},
    )
    assert {l.material_code: l.net for l in net} == {
        "CR0001": Decimal("0"), "CR0002": Decimal("30")}


# ── 倒推下单周 + 起订量 ──────────────────────────────────────────────────


def test_the_order_date_is_the_need_date_minus_the_lead_time():
    orders = plan_orders(
        [ComponentDemand("CR0001", W3, Decimal("100"), net=Decimal("100"))],
        {"CR0001": SupplyParams(partner_code="SUP-A", lead_time_days=14)},
    )
    assert orders[0].order_date == date(2026, 9, 5)      # 09-19 减 14 天
    assert orders[0].suggested_qty == Decimal("100")
    assert orders[0].lead_time_missing is False


def test_a_missing_lead_time_is_flagged_not_treated_as_zero():
    """★缺提前期按 0 算但必须标出来 —— 不标的话采购会以为随时能到货。"""
    orders = plan_orders(
        [ComponentDemand("CR0001", W3, Decimal("100"), net=Decimal("100"))],
        {"CR0001": SupplyParams(partner_code="SUP-A", lead_time_days=None)},
    )
    assert orders[0].order_date == W3
    assert orders[0].lead_time_missing is True


def test_a_material_with_no_supplier_at_all_is_flagged_too():
    orders = plan_orders(
        [ComponentDemand("CR0001", W3, Decimal("100"), net=Decimal("100"))], {})
    assert orders[0].partner_code is None
    assert orders[0].supplier_missing is True
    assert orders[0].suggested_qty == Decimal("100")     # 数照算，只是不知道找谁买


def test_a_requirement_below_the_moq_is_raised_to_it():
    orders = plan_orders(
        [ComponentDemand("CR0001", W3, Decimal("40"), net=Decimal("40"))],
        {"CR0001": SupplyParams(partner_code="SUP-A", lead_time_days=7,
                                moq=Decimal("100"))},
    )
    assert orders[0].suggested_qty == Decimal("100")
    assert orders[0].raised_to_moq == Decimal("60")


def test_an_order_multiple_rounds_up_after_the_moq():
    orders = plan_orders(
        [ComponentDemand("CR0001", W3, Decimal("130"), net=Decimal("130"))],
        {"CR0001": SupplyParams(partner_code="SUP-A", lead_time_days=7,
                                moq=Decimal("100"), order_multiple=Decimal("50"))},
    )
    assert orders[0].suggested_qty == Decimal("150")     # 130 → 上取到 50 的倍数


def test_a_fully_covered_week_produces_no_order():
    """净需求为 0 的周不该生成建议行 —— 采购看到一堆 0 就不看了。"""
    orders = plan_orders(
        [ComponentDemand("CR0001", W1, Decimal("30"), net=Decimal("0"))],
        {"CR0001": SupplyParams(partner_code="SUP-A", lead_time_days=7)},
    )
    assert orders == []


def test_an_order_date_already_in_the_past_is_flagged():
    """提前期倒推到今天之前 = 已经来不及了，必须显式告诉采购。"""
    orders = plan_orders(
        [ComponentDemand("CR0001", W1, Decimal("100"), net=Decimal("100"))],
        {"CR0001": SupplyParams(partner_code="SUP-A", lead_time_days=60)},
        today=date(2026, 9, 1),
    )
    assert orders[0].order_date < date(2026, 9, 1)
    assert orders[0].order_date_passed is True
