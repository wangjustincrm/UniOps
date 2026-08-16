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


# ── 端点：生成 / 回看 / 标状态 ────────────────────────────────────────────
#
# mdm-api 的两个调用打桩：这一层要验的是接线与持久化，BOM 展开本身在上面
# 的纯函数用例里已经钉死了。

import uuid as _uuid                                                    # noqa: E402
from datetime import datetime, timezone                                 # noqa: E402

from app.api.v1 import purchase as purchase_module                      # noqa: E402
from app.services import purchase_service                               # noqa: E402
from app.models.demand import MrpDemand                                 # noqa: E402
from app.models.mps import MrpMpsRun                                    # noqa: E402


async def _plan_in_force(db_session, *, product="S0093", week=W1, qty="100"):
    run = MrpMpsRun(
        run_no=f"MPS-{_uuid.uuid4().hex[:6].upper()}",
        forecast_version_id=_uuid.uuid4(), horizon_start_month="2026-09",
        horizon_months=18, status="released", safety_margin_fraction=0,
        is_default=True, released_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(MrpDemand(
        source_run_id=run.id, demand_type="mps", material_code=product,
        demand_month="2026-09", plan_week_start=week, qty=Decimal(qty)))
    await db_session.commit()
    return run


def _stub_mdm(monkeypatch, *, adjacency, supply):
    async def _adjacency(token, product_codes, on_date):
        return adjacency, []

    async def _supply(token):
        return supply

    monkeypatch.setattr(purchase_service, "fetch_bom_adjacency", _adjacency)
    monkeypatch.setattr(purchase_service, "fetch_supply_params", _supply)


@pytest.mark.anyio
async def test_generating_a_run_stores_the_suggestions(
    client, db_session, admin_token, monkeypatch,
):
    await _plan_in_force(db_session)
    _stub_mdm(monkeypatch,
              adjacency={"S0093": [("CR0001", Decimal("2"))]},
              supply={"CR0001": SupplyParams(partner_code="SUP-A", lead_time_days=7)})

    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post("/api/v1/purchase/runs", headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["run_no"].startswith("PUR-")
    assert len(body["lines"]) == 1
    line = body["lines"][0]
    assert line["material_code"] == "CR0001"
    assert Decimal(line["suggested_qty"]) == Decimal("200")     # 100 x 2，无损耗无库存
    assert line["partner_code"] == "SUP-A"
    assert line["status"] == "pending"

    # 回看的是存下来的那一份，不是重算
    again = await client.get(f"/api/v1/purchase/runs/{body['id']}", headers=headers)
    assert again.status_code == 200
    assert again.json()["lines"] == body["lines"]


@pytest.mark.anyio
async def test_generating_without_a_plan_in_force_is_409(
    client, db_session, admin_token, monkeypatch,
):
    """★没有生效计划时返回空 run 会被读成「不用买」，而事实是「还没有计划」。"""
    _stub_mdm(monkeypatch, adjacency={}, supply={})
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post("/api/v1/purchase/runs", headers=headers)
    assert r.status_code == 409, r.text
    assert "no production plan" in r.text.lower()


@pytest.mark.anyio
async def test_the_run_snapshots_the_loss_rates_it_used(
    client, db_session, admin_token, monkeypatch,
):
    await _plan_in_force(db_session)
    _stub_mdm(monkeypatch,
              adjacency={"S0093": [("CR0001", Decimal("1"))]},
              supply={"CR0001": SupplyParams(partner_code="SUP-A", lead_time_days=7)})
    headers = {"Authorization": f"Bearer {admin_token}"}
    await client.put("/api/v1/params/raw_material_loss_rate", json={"value": 0.02},
                     headers=headers)

    body = (await client.post("/api/v1/purchase/runs", headers=headers)).json()
    assert Decimal(body["raw_material_loss_rate"]) == Decimal("0.02")
    assert Decimal(body["lines"][0]["suggested_qty"]) == Decimal("102")

    # 之后改设置，旧 run 的记载不许跟着变
    await client.put("/api/v1/params/raw_material_loss_rate", json={"value": 0.10},
                     headers=headers)
    after = (await client.get(f"/api/v1/purchase/runs/{body['id']}", headers=headers)).json()
    assert Decimal(after["raw_material_loss_rate"]) == Decimal("0.02")
    assert Decimal(after["lines"][0]["suggested_qty"]) == Decimal("102")


@pytest.mark.anyio
async def test_a_line_can_be_marked_ordered(client, db_session, admin_token, monkeypatch):
    await _plan_in_force(db_session)
    _stub_mdm(monkeypatch,
              adjacency={"S0093": [("CR0001", Decimal("1"))]},
              supply={"CR0001": SupplyParams(partner_code="SUP-A", lead_time_days=7)})
    headers = {"Authorization": f"Bearer {admin_token}"}
    run = (await client.post("/api/v1/purchase/runs", headers=headers)).json()
    line_id = run["lines"][0]["id"]

    ok = await client.patch(f"/api/v1/purchase/runs/{run['id']}/lines/{line_id}",
                            json={"status": "ordered"}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "ordered"

    bad = await client.patch(f"/api/v1/purchase/runs/{run['id']}/lines/{line_id}",
                             json={"status": "done"}, headers=headers)
    assert bad.status_code == 422


@pytest.mark.anyio
async def test_the_export_carries_every_suggestion_and_its_warnings(
    client, db_session, admin_token, monkeypatch,
):
    """采购是拿着表格去下单的 —— 屏幕上有、导出里没有，等于没有。"""
    import io as _io

    import openpyxl

    await _plan_in_force(db_session)
    _stub_mdm(monkeypatch,
              adjacency={"S0093": [("CR0001", Decimal("1"))]},
              supply={})                     # 没有供应商 → 该行必须带告警
    headers = {"Authorization": f"Bearer {admin_token}"}
    run = (await client.post("/api/v1/purchase/runs", headers=headers)).json()

    r = await client.get(f"/api/v1/purchase/runs/{run['id']}/export", headers=headers)
    assert r.status_code == 200, r.text
    assert "spreadsheetml" in r.headers["content-type"]
    assert run["run_no"] in r.headers["content-disposition"]

    sheet = openpyxl.load_workbook(_io.BytesIO(r.content)).active
    header = [c.value for c in sheet[1]]
    assert header[0] == "Order by" and "Attention" in header

    body_rows = [[c.value for c in row] for row in sheet.iter_rows(min_row=2)
                 if row[2].value]
    assert len(body_rows) == len(run["lines"])
    # 告警是文字列，不是颜色 —— 表格会被筛选、转发、粘进别的表，颜色都留不下
    assert any("no supplier" in (r[-1] or "") for r in body_rows)


@pytest.mark.anyio
async def test_a_failed_bom_fetch_is_not_reported_as_a_missing_bom(
    client, db_session, admin_token, monkeypatch,
):
    """★真踩过：explode 的查询参数名写错 → 每个请求 422 → 被吞成「这个产品
    没有 BOM」→ run 报告「计划里 6 个成品全都没有 BOM、0 行建议」。

    「问不到」和「确实没有 BOM」必须分开：后者是能补的数据缺口，前者说明
    下面的数字不完整、不能拿去下单。"""
    await _plan_in_force(db_session)

    async def _all_requests_fail(token, product_codes, on_date):
        return {}, list(product_codes)

    async def _no_supply(token):
        return {}

    monkeypatch.setattr(purchase_service, "fetch_bom_adjacency", _all_requests_fail)
    monkeypatch.setattr(purchase_service, "fetch_supply_params", _no_supply)

    headers = {"Authorization": f"Bearer {admin_token}"}
    run = (await client.post("/api/v1/purchase/runs", headers=headers)).json()
    assert run["stats"]["bom_fetch_failed"] == 1
    assert run["lines"] == []
