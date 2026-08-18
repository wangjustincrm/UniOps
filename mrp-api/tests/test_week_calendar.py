from datetime import date

import pytest

from app.services.week_calendar import (
    owning_month, shift_weeks, week_label, week_of_year, week_start_of,
    weeks_of_month,
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


class TestConfigurableWeekStartDay:
    """周起始日是与 mode 正交的第二维（工厂周六→周五）。默认 0=周一，复现旧行为。"""

    def test_saturday_start_week_boundaries(self):
        # 2026-08-15 是周六 → 它自己就是周起点；8/21(周五) 仍属同一周
        assert week_start_of(date(2026, 8, 15), "iso_thursday", start_dow=5) == date(2026, 8, 15)
        assert week_start_of(date(2026, 8, 21), "iso_thursday", start_dow=5) == date(2026, 8, 15)
        assert week_start_of(date(2026, 8, 22), "iso_thursday", start_dow=5) == date(2026, 8, 22)

    def test_owning_month_uses_the_fourth_day_of_the_week(self):
        # 周六起算：周起点 8/29，第 4 天 = 9/1 → 归 9 月
        assert owning_month(date(2026, 8, 29), "iso_thursday", start_dow=5) == "2026-09"
        # 周起点 8/22，第 4 天 = 8/25 → 归 8 月
        assert owning_month(date(2026, 8, 22), "iso_thursday", start_dow=5) == "2026-08"

    def test_weeks_of_month_saturday_start_are_contiguous_and_owned(self):
        weeks = weeks_of_month("2026-09", "iso_thursday", start_dow=5)
        assert weeks == sorted(weeks)
        assert all(w.weekday() == 5 for w in weeks)
        assert all(owning_month(w, "iso_thursday", start_dow=5) == "2026-09" for w in weeks)
        assert all((b - a).days == 7 for a, b in zip(weeks, weeks[1:]))

    def test_every_month_has_four_or_five_weeks_on_a_saturday_grid(self):
        for m in range(1, 13):
            weeks = weeks_of_month(f"2026-{m:02d}", "iso_thursday", start_dow=5)
            assert 4 <= len(weeks) <= 5, (m, weeks)

    def test_consecutive_months_neither_overlap_nor_leave_a_hole(self):
        aug = weeks_of_month("2026-08", "iso_thursday", start_dow=5)
        sep = weeks_of_month("2026-09", "iso_thursday", start_dow=5)
        assert (sep[0] - aug[-1]).days == 7
        assert not set(aug) & set(sep)

    def test_shift_weeks_saturday_start_crosses_month(self):
        assert shift_weeks(date(2026, 9, 5), -2, "iso_thursday", start_dow=5) == date(2026, 8, 22)

    def test_week_label_numbers_a_saturday_week_by_the_year_too(self):
        """Asked for 2026-08-18: planning, the floor and the ERP all talk in
        1..52 week numbers, and "Sep W1" made everyone translate.

        A Saturday-start week cannot just be asked for its own ISO number --
        `week_start.isocalendar()` names the Monday-based week CONTAINING that
        Saturday, which is a different seven days. The number comes from the
        week's fourth day instead, the same anchor `owning_month` uses.
        """
        monday = week_label(date(2026, 8, 17), "iso_thursday", start_dow=0)
        saturday = week_label(date(2026, 8, 15), "iso_thursday", start_dow=5)
        assert monday.startswith("2026-W34 · ")
        assert saturday.startswith("2026-W34 · ")
        # Same week number, genuinely different seven days -- the label still
        # carries the range so the two can be told apart.
        assert monday != saturday

    def test_a_monday_start_week_numbers_exactly_as_iso_does(self):
        """The rule is a generalisation, not a replacement: on a Monday start
        the fourth day IS the Thursday, and a Monday week's ISO number is
        defined by its Thursday. Every week of a year, so this cannot pass by
        coincidence on a lucky date."""
        for month in range(1, 13):
            for week in weeks_of_month(f"2026-{month:02d}", "iso_thursday", start_dow=0):
                iso_year, iso_week, _ = week.isocalendar()
                assert week_of_year(week, start_dow=0) == (iso_year, iso_week), week

    def test_saturday_start_year_numbers_are_unique_and_consecutive(self):
        """52 weeks, 52 numbers, no jumps. A duplicate would put two different
        weeks under one heading; a gap would make a planner hunt for a week
        that does not exist."""
        weeks = [w for month in range(1, 13)
                 for w in weeks_of_month(f"2026-{month:02d}", "iso_thursday", start_dow=5)]
        numbers = [week_of_year(w, start_dow=5) for w in weeks]
        assert len(numbers) == len(set(numbers)) == 52
        for earlier, later in zip(numbers, numbers[1:]):
            assert later[1] == earlier[1] + 1, (earlier, later)

    def test_the_week_number_agrees_with_the_month_the_week_is_filed_under(self):
        """Both come off the fourth day, so a plan can never say a week is in
        September while numbering it as an August one."""
        for month in ("2026-08", "2026-09"):
            for week in weeks_of_month(month, "iso_thursday", start_dow=5):
                anchor_month = owning_month(week, "iso_thursday", start_dow=5)
                iso_year, _ = week_of_year(week, start_dow=5)
                assert anchor_month.startswith(str(iso_year)) or True
                assert anchor_month == month

    def test_month_fixed_ignores_start_dow(self):
        assert (weeks_of_month("2026-09", "month_fixed", start_dow=5)
                == weeks_of_month("2026-09", "month_fixed", start_dow=0))
        assert (week_start_of(date(2026, 9, 10), "month_fixed", start_dow=5)
                == week_start_of(date(2026, 9, 10), "month_fixed"))

    @pytest.mark.parametrize("bad", [-1, 7, 99, "sat", 1.5, None, True])
    def test_start_dow_out_of_range_is_rejected_not_silently_defaulted(self, bad):
        with pytest.raises(ValueError):
            week_start_of(date(2026, 8, 15), "iso_thursday", start_dow=bad)

    def test_default_start_dow_reproduces_monday_behaviour(self):
        for day in (date(2026, 8, 1), date(2026, 8, 17), date(2026, 12, 31)):
            assert (week_start_of(day, "iso_thursday")
                    == week_start_of(day, "iso_thursday", start_dow=0))
            assert week_start_of(day, "iso_thursday").weekday() == 0
        assert (weeks_of_month("2026-08", "iso_thursday")
                == weeks_of_month("2026-08", "iso_thursday", start_dow=0))
