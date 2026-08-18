"""mrp11 的新列存在性与默认值。

这些列分两类，混淆会出事：
- `week_start_dow` / `frozen_months` 是**快照项**（run 记下生成时的设置，回放
  历史计划一律读 run 自己的值，绝不读当前设置）；
- 其余五列是引擎每次算出来的标记，重算就该覆盖。
"""
from app.models.mps import MrpMpsLine, MrpMpsRun

_LINE_COLUMNS = (
    "surplus_qty", "carry_in_qty", "late_production",
    "surplus_expiry_risk", "below_min_lot", "covered_by_carry",
)


def test_run_snapshot_columns_exist_with_the_documented_defaults():
    cols = MrpMpsRun.__table__.c
    assert cols.week_start_dow.default.arg == 0          # 0=Monday: 存量 run 按周一回放
    assert str(cols.week_start_dow.server_default.arg) == "0"
    assert cols.frozen_months.default.arg == 3           # 锁定区默认 3 个月
    assert str(cols.frozen_months.server_default.arg) == "3"


def test_line_planning_flag_columns_exist():
    cols = MrpMpsLine.__table__.c
    for name in _LINE_COLUMNS:
        assert name in cols, name


def test_line_flag_columns_default_to_zero_or_false():
    cols = MrpMpsLine.__table__.c
    for name in ("surplus_qty", "carry_in_qty"):
        assert str(cols[name].server_default.arg) == "0", name
    for name in ("late_production", "surplus_expiry_risk", "below_min_lot", "covered_by_carry"):
        assert str(cols[name].server_default.arg) == "false", name


def test_new_columns_are_not_nullable():
    """全部带 server_default 的 NOT NULL —— 纯增量，可对已有数据的生产库直接跑。"""
    cols = MrpMpsLine.__table__.c
    for name in _LINE_COLUMNS:
        assert cols[name].nullable is False, name
    assert MrpMpsRun.__table__.c.week_start_dow.nullable is False
    assert MrpMpsRun.__table__.c.frozen_months.nullable is False
