"""Phase 1C 的两个前置 + 损耗率参数。

1C（物料展开 → 采购建议）会从净需求取数。意向产品只有名字、没有 ERP 物料码，
它的占位码进了物料展开就是**给一个不存在的产品下采购建议**；MPS 侧早就挡掉了，
净需求侧一直没挡（1A 时没有消费者，所以潜伏至今）。
"""
from decimal import Decimal

import pytest

from app.services.loss_rate import applicable_loss_rate, inflate_for_loss


# ── 损耗率：适用判定与放大 ───────────────────────────────────────────────
#
# 用户决策：BOM 视为精准数据，损耗在 MRP 运算时按配置的率统一放大。只两种率：
# 原料与包材。判定按物料类别 —— 包材（CP*）用包材率，其余被消耗物料用原料率。


@pytest.mark.parametrize("code,expected", [
    ("CP0001", "packaging"),      # 包材
    ("CR0001", "raw"),            # 原辅料
    ("CS0001", "raw"),            # 半成品粉
    ("CW0001", "raw"),
    ("S0093", "raw"),             # 兜底：不认识的前缀按原料算
])
def test_the_applicable_rate_is_chosen_by_material_category(code, expected):
    rates = {"raw": Decimal("0.02"), "packaging": Decimal("0.05")}
    assert applicable_loss_rate(code, rates) == rates[expected]


def test_a_zero_rate_leaves_the_quantity_untouched():
    """★包材率初值必须是 0：制粉/干混 BOM 无损耗，而部分包装 BOM 已把损耗
    按包材类型分档写进用量了（S0093 700g*6：理论 600 罐，BOM 写 610）。
    再乘一次就是重复放大。"""
    assert inflate_for_loss(Decimal("600"), Decimal("0")) == Decimal("600")


def test_a_rate_inflates_the_requirement():
    assert inflate_for_loss(Decimal("100"), Decimal("0.02")) == Decimal("102")


def test_inflation_never_reduces_a_requirement():
    """负率是配置错误，不是「少买一点」的手段 —— 少买会停线。"""
    with pytest.raises(ValueError):
        inflate_for_loss(Decimal("100"), Decimal("-0.1"))


# ── 参数端点 ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_loss_rates_default_to_zero(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.get("/api/v1/params", headers=headers)
    assert r.status_code == 200
    body = r.json()
    # 未设置时不写死在库里，取数时落到 0
    assert body.get("raw_material_loss_rate") in (None, 0)
    assert body.get("packaging_loss_rate") in (None, 0)


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["raw_material_loss_rate", "packaging_loss_rate"])
async def test_loss_rates_round_trip(client, admin_token, key):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.put(f"/api/v1/params/{key}", json={"value": 0.02}, headers=headers)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/params", headers=headers)).json()[key] == 0.02


@pytest.mark.anyio
@pytest.mark.parametrize("bad", [-0.01, 1.5, "2%", None, True])
async def test_loss_rates_reject_bad_values(client, admin_token, bad):
    """上限 1.0（=100%）：超过它多半是把百分数当小数填了，而那会让采购量翻几倍。"""
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.put("/api/v1/params/raw_material_loss_rate",
                         json={"value": bad}, headers=headers)
    assert r.status_code == 422, r.text


@pytest.mark.anyio
async def test_the_resolver_defaults_both_rates_to_zero(db_session):
    from app.services.loss_rate import resolve_loss_rates

    rates = await resolve_loss_rates(db_session)
    assert rates == {"raw": Decimal("0"), "packaging": Decimal("0")}


# ── 前置一：净需求必须排除意向产品 ───────────────────────────────────────


@pytest.mark.anyio
async def test_net_requirement_excludes_intent_products(client, db_session, admin_token):
    """★意向产品只有名字、没有 ERP 物料码。它的占位码进了 1C 的物料展开，
    就是给一个不存在的产品下采购建议。MPS 侧早就挡了，净需求侧一直没挡。"""
    import uuid
    from datetime import datetime, timezone

    from app.models.forecast import ForecastLine, ForecastVersion

    version = ForecastVersion(
        version_no=f"FCV-INTENT-{uuid.uuid4().hex[:6].upper()}",
        status="confirmed", horizon_start_month="2026-09", horizon_months=2,
        confirmed_at=datetime.now(timezone.utc),
    )
    db_session.add(version)
    await db_session.flush()
    db_session.add_all([
        ForecastLine(version_id=version.id, material_code="S0093",
                     month="2026-09", qty=Decimal("100")),
        # 冻结快照上的判据是 is_intent 列，不是码前缀 —— 前缀只在冻结那一刻用
        ForecastLine(version_id=version.id, material_code="INTENT-a1b2c3d4",
                     month="2026-09", qty=Decimal("500"),
                     is_intent=True, intent_name="New Formula 3"),
    ])
    await db_session.commit()

    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.get(f"/api/v1/net-requirement?version_id={version.id}", headers=headers)
    assert r.status_code == 200, r.text
    codes = [row["material_code"] for row in r.json()["items"]]
    assert "S0093" in codes, "fixture guard: the real product must be there"
    assert not [c for c in codes if c.startswith("INTENT-")], codes


@pytest.mark.anyio
async def test_asking_for_an_intent_material_by_code_is_404(client, db_session, admin_token):
    """按码直查也要挡 —— 否则列表挡住了、单查还是漏。"""
    import uuid
    from datetime import datetime, timezone

    from app.models.forecast import ForecastLine, ForecastVersion

    version = ForecastVersion(
        version_no=f"FCV-INTENT-{uuid.uuid4().hex[:6].upper()}",
        status="confirmed", horizon_start_month="2026-09", horizon_months=2,
        confirmed_at=datetime.now(timezone.utc),
    )
    db_session.add(version)
    await db_session.flush()
    db_session.add(ForecastLine(version_id=version.id, material_code="INTENT-a1b2c3d4",
                                month="2026-09", qty=Decimal("500"),
                                is_intent=True, intent_name="New Formula 3"))
    await db_session.commit()

    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.get(
        f"/api/v1/net-requirement?version_id={version.id}&material_code=INTENT-a1b2c3d4",
        headers=headers)
    assert r.status_code == 404, r.text
