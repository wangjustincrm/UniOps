"""周期排期的日期算法 —— 纯函数,不碰 DB。"""
from datetime import date

import pytest

from app.services.agreement_schedule import (
    MAX_PERIOD_ROWS, TooManyPeriods, build_period_rows,
)


def test_monthly_generates_one_row_per_calendar_month():
    rows = build_period_rows(
        recurring_type="monthly", valid_from=date(2026, 1, 1),
        valid_to=date(2026, 3, 31), expected_invoice_day=5, anchor_month=None,
    )
    assert [r.period_label for r in rows] == ["2026-01", "2026-02", "2026-03"]
    assert [r.expected_date for r in rows] == [
        date(2026, 1, 5), date(2026, 2, 5), date(2026, 3, 5)]
    assert [r.sequence for r in rows] == [1, 2, 3]


def test_monthly_day_31_clamps_to_end_of_february():
    rows = build_period_rows(
        recurring_type="monthly", valid_from=date(2026, 1, 1),
        valid_to=date(2026, 2, 28), expected_invoice_day=31, anchor_month=None,
    )
    assert rows[0].expected_date == date(2026, 1, 31)
    assert rows[1].expected_date == date(2026, 2, 28)


def test_monthly_day_31_clamps_to_february_29_in_a_leap_year():
    rows = build_period_rows(
        recurring_type="monthly", valid_from=date(2028, 2, 1),
        valid_to=date(2028, 2, 29), expected_invoice_day=31, anchor_month=None,
    )
    assert rows[0].expected_date == date(2028, 2, 29)


def test_first_period_is_skipped_when_its_invoice_day_precedes_valid_from():
    # 协议 8/20 生效、每月 5 号到票 —— 8 月那张票在生效前就该到了,不该排。
    rows = build_period_rows(
        recurring_type="monthly", valid_from=date(2026, 8, 20),
        valid_to=date(2026, 10, 31), expected_invoice_day=5, anchor_month=None,
    )
    assert [r.period_label for r in rows] == ["2026-09", "2026-10"]
    assert rows[0].sequence == 1     # 序号从留下的第一期重新起算


def test_last_period_is_kept_even_when_its_invoice_day_falls_after_valid_to():
    # 月结票总在期末之后才到 —— 期起始日 <= valid_to 就该排。
    rows = build_period_rows(
        recurring_type="monthly", valid_from=date(2026, 1, 1),
        valid_to=date(2026, 3, 2), expected_invoice_day=25, anchor_month=None,
    )
    assert [r.period_label for r in rows] == ["2026-01", "2026-02", "2026-03"]
    assert rows[-1].expected_date == date(2026, 3, 25)   # 晚于 valid_to,仍保留


def test_quarterly_follows_anchor_month_not_calendar_quarters():
    # 决策 7 的核心场景:起始月 2 月 → 2/5/8/11(日历季度会是 1/4/7/10),
    # 且 anchor_month 与 valid_from 的月份**不同**,证明它没被 valid_from 推导。
    rows = build_period_rows(
        recurring_type="quarterly", valid_from=date(2026, 1, 5),
        valid_to=date(2026, 12, 31), expected_invoice_day=10, anchor_month=2,
    )
    assert [r.period_label for r in rows] == ["2026-02", "2026-05", "2026-08", "2026-11"]
    assert [r.expected_date for r in rows] == [
        date(2026, 2, 10), date(2026, 5, 10), date(2026, 8, 10), date(2026, 11, 10)]


def test_quarterly_covering_period_is_dropped_when_its_invoice_day_precedes_valid_from():
    # valid_from=6/1 落在 2026-05 那一期里,但该期的到票日是 5/10 —— 早于生效日,
    # 由"首期跳过"规则丢掉。两条规则叠加后的结果必须是确定的,不是二选一。
    rows = build_period_rows(
        recurring_type="quarterly", valid_from=date(2026, 6, 1),
        valid_to=date(2026, 12, 31), expected_invoice_day=10, anchor_month=2,
    )
    assert [r.period_label for r in rows] == ["2026-08", "2026-11"]
    assert rows[0].sequence == 1


def test_quarterly_period_spanning_a_year_boundary():
    rows = build_period_rows(
        recurring_type="quarterly", valid_from=date(2026, 11, 1),
        valid_to=date(2027, 6, 30), expected_invoice_day=10, anchor_month=2,
    )
    assert [r.period_label for r in rows] == ["2026-11", "2027-02", "2027-05"]


def test_yearly_uses_anchor_month():
    rows = build_period_rows(
        recurring_type="yearly", valid_from=date(2026, 1, 10),
        valid_to=date(2028, 12, 31), expected_invoice_day=15, anchor_month=3,
    )
    assert [r.period_label for r in rows] == ["2026-03", "2027-03", "2028-03"]
    assert rows[0].expected_date == date(2026, 3, 15)


def test_weekly_uses_iso_weekday_and_iso_week_labels():
    # 2026-01-01 是周四;其 ISO 周是 2026-W01(周一 = 2025-12-29)。
    rows = build_period_rows(
        recurring_type="weekly", valid_from=date(2026, 1, 5),
        valid_to=date(2026, 1, 25), expected_invoice_day=3, anchor_month=None,
    )
    assert rows[0].period_label.startswith("2026-W")
    # day 3 = 周三
    assert all(r.expected_date.isoweekday() == 3 for r in rows)
    assert len(rows) == 3


def test_row_count_over_the_cap_is_rejected():
    with pytest.raises(TooManyPeriods):
        build_period_rows(
            recurring_type="weekly", valid_from=date(2026, 1, 1),
            valid_to=date(2040, 1, 1), expected_invoice_day=1, anchor_month=None,
        )


def test_cap_value_is_500():
    assert MAX_PERIOD_ROWS == 500


def test_unknown_recurring_type_raises():
    with pytest.raises(ValueError):
        build_period_rows(
            recurring_type="fortnightly", valid_from=date(2026, 1, 1),
            valid_to=date(2026, 6, 1), expected_invoice_day=1, anchor_month=None,
        )
