"""版本差异：这一版和上一版差在哪。

计划员每月对着 79 列的矩阵重新核一遍是不现实的 —— 「延续而非替换」的本质
诉求是**一眼看出这次和上次差在哪**。差异按**矩阵格子**（产品 × 计划周）算，
和屏幕上看到的粒度一致；换成需求月粒度，标记就落不到格子上。
"""
from datetime import date
from decimal import Decimal

import pytest

from app.api.v1 import mps as mps_module
from app.api.v1.mps import diff_cells
from tests.test_mps_api import (
    _confirmed_version, _factory_rule, _future_month, _no_shelf_life,
)


class _Line:
    """MrpMpsLine 的最小替身 —— `diff_cells` 是纯函数，不该为了测它建库。"""

    def __init__(self, code, week, qty, *, gap=False):
        self.material_code = code
        self.plan_week_start = week
        self.qty = Decimal(qty)
        self.capacity_gap = gap


W1, W2 = date(2026, 9, 5), date(2026, 9, 12)


def test_an_unchanged_plan_has_no_cells():
    lines = [_Line("A", W1, "30"), _Line("B", W2, "20")]
    cells, summary = diff_cells(lines, [_Line("A", W1, "30"), _Line("B", W2, "20")])
    assert cells == []
    assert summary == {"products_changed": 0, "weeks_changed": 0, "total_delta": Decimal("0")}


def test_an_increase_and_a_decrease_are_both_reported():
    cells, summary = diff_cells(
        [_Line("A", W1, "50"), _Line("B", W2, "10")],
        [_Line("A", W1, "30"), _Line("B", W2, "20")],
    )
    assert [(c["material_code"], c["plan_week_start"], c["before"], c["after"], c["delta"])
            for c in cells] == [
        ("A", W1, Decimal("30"), Decimal("50"), Decimal("20")),
        ("B", W2, Decimal("20"), Decimal("10"), Decimal("-10")),
    ]
    assert summary["products_changed"] == 2
    assert summary["weeks_changed"] == 2
    assert summary["total_delta"] == Decimal("10")


def test_a_cell_that_only_exists_now_reads_as_from_zero():
    cells, _ = diff_cells([_Line("A", W1, "40")], [])
    assert cells[0]["before"] == Decimal("0")
    assert cells[0]["after"] == Decimal("40")


def test_a_cell_that_has_vanished_is_still_reported():
    """★这一版没有的格子在矩阵里根本没有行 —— 而「某个品被整个挪走了」正是
    最该被看见的变化。漏掉它，差异视图就只剩一半。"""
    cells, summary = diff_cells([], [_Line("A", W1, "40")])
    assert cells[0]["before"] == Decimal("40")
    assert cells[0]["after"] == Decimal("0")
    assert cells[0]["delta"] == Decimal("-40")
    assert summary["total_delta"] == Decimal("-40")


def test_capacity_gap_lines_are_not_production_and_never_diffed():
    """缺口是未满足的需求，不是产量。把它算进差异，一个从未生产过的量会
    显示成「减少」。"""
    cells, _ = diff_cells([_Line("A", W1, "30")],
                          [_Line("A", W1, "30"), _Line("A", W1, "999", gap=True)])
    assert cells == []


def test_several_lines_on_one_cell_are_summed_before_comparing():
    """一条摊平的生产线在同一周可能有多行（不同需求月）。"""
    cells, _ = diff_cells([_Line("A", W1, "20"), _Line("A", W1, "10")],
                          [_Line("A", W1, "30")])
    assert cells == []


def test_cells_are_ordered_by_product_then_week():
    cells, _ = diff_cells(
        [_Line("B", W1, "5"), _Line("A", W2, "5"), _Line("A", W1, "5")], [])
    assert [(c["material_code"], c["plan_week_start"]) for c in cells] == [
        ("A", W1), ("A", W2), ("B", W1)]


# ── 端点 ─────────────────────────────────────────────────────────────────


async def _run_for(client, db_session, headers, *, start, qty="100"):
    version, _ = await _confirmed_version(db_session, start=start, months=2,
                                          monthly_qty=qty)
    r = await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.anyio
async def test_diff_defaults_to_the_previous_version_of_the_same_group(
    client, db_session, admin_token, monkeypatch,
):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    await _factory_rule(client, headers)
    month = _future_month(1)

    v1 = await _run_for(client, db_session, headers, start=month, qty="100")
    v2 = await _run_for(client, db_session, headers, start=month, qty="150")

    r = await client.get(f"/api/v1/mps/runs/{v2['id']}/diff", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["baseline_run_id"] == v1["id"]
    assert body["cells"], "the two versions differ, so the diff must not be empty"
    assert Decimal(body["summary"]["total_delta"]) > 0     # 150 > 100


@pytest.mark.anyio
async def test_the_first_version_has_no_baseline_and_that_is_not_an_error(
    client, db_session, admin_token, monkeypatch,
):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    await _factory_rule(client, headers)

    only = await _run_for(client, db_session, headers, start=_future_month(1))
    r = await client.get(f"/api/v1/mps/runs/{only['id']}/diff", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["baseline_run_id"] is None
    assert r.json()["cells"] == []


@pytest.mark.anyio
async def test_diff_accepts_an_explicit_baseline(
    client, db_session, admin_token, monkeypatch,
):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    await _factory_rule(client, headers)
    month = _future_month(1)

    v1 = await _run_for(client, db_session, headers, start=month, qty="100")
    v2 = await _run_for(client, db_session, headers, start=month, qty="150")
    v3 = await _run_for(client, db_session, headers, start=month, qty="200")

    r = await client.get(f"/api/v1/mps/runs/{v3['id']}/diff?against={v1['id']}",
                         headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["baseline_run_id"] == v1["id"]
    assert r.json()["baseline_run_no"] == v1["run_no"]
    # 默认基准会是 v2；显式指定必须真的换掉基准
    default_body = (await client.get(f"/api/v1/mps/runs/{v3['id']}/diff",
                                     headers=headers)).json()
    assert default_body["baseline_run_id"] == v2["id"]
    assert default_body["summary"] != r.json()["summary"]


@pytest.mark.anyio
async def test_an_unknown_baseline_is_404(client, db_session, admin_token, monkeypatch):
    import uuid as _uuid

    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    await _factory_rule(client, headers)
    run = await _run_for(client, db_session, headers, start=_future_month(1))

    r = await client.get(f"/api/v1/mps/runs/{run['id']}/diff?against={_uuid.uuid4()}",
                         headers=headers)
    assert r.status_code == 404, r.text
