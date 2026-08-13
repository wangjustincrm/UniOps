from datetime import date

import pytest

from app.services.week_calendar import (
    owning_month, shift_weeks, week_label, week_start_of, weeks_of_month,
)


class TestIsoThursday:
    """跨月周归【周四所在月】。2026-07-27(周一)~08-02(周日) 的周四是 07-30 → 归 7 月。"""

    def test_straddling_week_goes_to_the_month_holding_its_thursday(self):
        assert owning_month(date(2026, 7, 27), "iso_thursday") == "2026-07"
        # 2026-08-31(周一)~09-06 的周四是 09-03 → 归 9 月
        assert owning_month(date(2026, 8, 31), "iso_thursday") == "2026-09"

    def test_weeks_of_month_are_mondays_in_order(self):
        weeks = weeks_of_month("2026-08", "iso_thursday")
        assert all(w.weekday() == 0 for w in weeks)
        assert weeks == sorted(weeks)
        assert all(owning_month(w, "iso_thursday") == "2026-08" for w in weeks)

    def test_every_month_has_four_or_five_weeks(self):
        for m in range(1, 13):
            assert len(weeks_of_month(f"2026-{m:02d}", "iso_thursday")) in (4, 5)

    def test_weeks_tile_the_year_without_gap_or_overlap(self):
        """一年 52/53 周，逐月拼接后不重不漏。"""
        all_weeks = [w for m in range(1, 13)
                     for w in weeks_of_month(f"2026-{m:02d}", "iso_thursday")]
        assert len(all_weeks) == len(set(all_weeks))
        for a, b in zip(all_weeks, all_weeks[1:]):
            assert (b - a).days == 7


class TestIsoFirstDay:
    """跨月周归【含月初那一天】的月：2026-07-27~08-02 含 8/1 → 归 8 月。"""

    def test_straddling_week_goes_to_the_new_month(self):
        assert owning_month(date(2026, 7, 27), "iso_first_day") == "2026-08"

    def test_non_straddling_week_is_unambiguous(self):
        assert owning_month(date(2026, 8, 10), "iso_first_day") == "2026-08"


class TestMonthFixed:
    """每月 1 号起每 7 天一周，永不跨月。"""

    def test_weeks_start_on_the_first_and_step_seven_days(self):
        assert weeks_of_month("2026-08", "month_fixed") == [
            date(2026, 8, 1), date(2026, 8, 8), date(2026, 8, 15),
            date(2026, 8, 22), date(2026, 8, 29),
        ]

    def test_last_week_is_the_short_remainder(self):
        # 2026-02 有 28 天 → 正好 4 周，无零头
        assert len(weeks_of_month("2026-02", "month_fixed")) == 4

    def test_owning_month_never_crosses(self):
        for w in weeks_of_month("2026-08", "month_fixed"):
            assert owning_month(w, "month_fixed") == "2026-08"


class TestShiftWeeks:
    def test_iso_shift_is_plain_seven_day_arithmetic(self):
        assert shift_weeks(date(2026, 8, 10), -4, "iso_thursday") == date(2026, 7, 13)
        assert shift_weeks(date(2026, 8, 10), 0, "iso_thursday") == date(2026, 8, 10)

    def test_month_fixed_shift_lands_on_a_real_week_start(self):
        """周长不恒为 7 天，所以不能直接减 7×n —— 必须落在该模式真实存在的周首日上。"""
        got = shift_weeks(date(2026, 8, 1), -1, "month_fixed")
        assert got in weeks_of_month("2026-07", "month_fixed")
        assert got == weeks_of_month("2026-07", "month_fixed")[-1]


class TestLabels:
    def test_iso_label_carries_week_number_and_date_range(self):
        assert week_label(date(2026, 8, 3), "iso_thursday") == "2026-W32 · Aug 3–9"

    def test_month_fixed_label_is_ordinal_within_month(self):
        assert week_label(date(2026, 8, 8), "month_fixed") == "Aug W2 · Aug 8–14"


def test_unknown_mode_is_rejected_not_silently_defaulted():
    with pytest.raises(ValueError):
        weeks_of_month("2026-08", "fiscal_445")


# --- Cases the brief's tests did not pin, added here (see task-1-report.md) ---


class TestIsoFirstDayWeeksOfMonth:
    """iso_first_day's weeks_of_month was never exercised by the brief —
    only owning_month was. Pin that it tiles the year too, and that the
    Dec/Jan straddling week (2026-12-28~2027-01-03, which contains 1/1)
    lands in January, not December."""

    def test_tiles_the_year_without_gap_or_overlap(self):
        all_weeks = [w for m in range(1, 13)
                     for w in weeks_of_month(f"2026-{m:02d}", "iso_first_day")]
        assert len(all_weeks) == len(set(all_weeks))
        for a, b in zip(all_weeks, all_weeks[1:]):
            assert (b - a).days == 7

    def test_year_boundary_straddle_goes_to_january(self):
        assert owning_month(date(2026, 12, 28), "iso_first_day") == "2027-01"
        assert date(2026, 12, 28) in weeks_of_month("2027-01", "iso_first_day")
        assert date(2026, 12, 28) not in weeks_of_month("2026-12", "iso_first_day")


class TestIsoThursdayYearBoundary:
    """Same straddling week under iso_thursday: its Thursday (2026-12-31)
    is still December, so — unlike iso_first_day — it stays in December."""

    def test_year_boundary_straddle_stays_in_december(self):
        assert owning_month(date(2026, 12, 28), "iso_thursday") == "2026-12"
        assert date(2026, 12, 28) in weeks_of_month("2026-12", "iso_thursday")
        assert date(2026, 12, 28) not in weeks_of_month("2027-01", "iso_thursday")


class TestMonthFixedRemainderLength:
    """The brief only checked February's remainder-free case (28 = 4x7).
    Pin the actual remainder lengths for a 31-day and a 30-day month."""

    def test_31_day_month_has_a_three_day_remainder(self):
        weeks = weeks_of_month("2026-01", "month_fixed")
        assert weeks[-1] == date(2026, 1, 29)
        assert len(weeks) == 5

    def test_30_day_month_has_a_two_day_remainder(self):
        weeks = weeks_of_month("2026-04", "month_fixed")
        assert weeks[-1] == date(2026, 4, 29)
        assert len(weeks) == 5


class TestWeekStartOf:
    """week_start_of is imported by the brief's test file but never
    actually asserted on. Pin its behaviour directly for all three modes."""

    def test_iso_modes_return_the_monday(self):
        assert week_start_of(date(2026, 8, 20), "iso_thursday") == date(2026, 8, 17)
        assert week_start_of(date(2026, 8, 20), "iso_first_day") == date(2026, 8, 17)

    def test_month_fixed_returns_the_nearest_anchor_at_or_before(self):
        assert week_start_of(date(2026, 8, 20), "month_fixed") == date(2026, 8, 15)
        assert week_start_of(date(2026, 8, 31), "month_fixed") == date(2026, 8, 29)


class TestShiftWeeksMonthFixedMultiStepAndYearBoundary:
    """The brief's month_fixed shift test only crosses one month boundary
    by one week. Pin a multi-step shift that crosses several month
    boundaries, and one that crosses a year boundary."""

    def test_multi_step_shift_crosses_several_months(self):
        # Aug 1 (index 0 of August) stepped back 6 lands on June's last week.
        assert shift_weeks(date(2026, 8, 1), -6, "month_fixed") == date(2026, 6, 29)

    def test_shift_crosses_a_year_boundary(self):
        assert shift_weeks(date(2026, 1, 1), -1, "month_fixed") == date(2025, 12, 29)


class TestUnknownModeRejectedEverywhere:
    """The brief only asserts weeks_of_month raises on a bad mode. Every
    public function must reject it the same way — a typo must never
    silently fall back to iso_thursday for *any* entry point."""

    def test_owning_month_rejects(self):
        with pytest.raises(ValueError):
            owning_month(date(2026, 8, 3), "fiscal_445")

    def test_week_start_of_rejects(self):
        with pytest.raises(ValueError):
            week_start_of(date(2026, 8, 3), "fiscal_445")

    def test_shift_weeks_rejects(self):
        with pytest.raises(ValueError):
            shift_weeks(date(2026, 8, 3), 1, "fiscal_445")

    def test_week_label_rejects(self):
        with pytest.raises(ValueError):
            week_label(date(2026, 8, 3), "fiscal_445")
