"""TDD tests for app/services/recurrence.py.

Step 1 (RED): All tests must FAIL before recurrence.py is implemented.
Step 2 (GREEN): Implement SeriesSpec + expand_series + build_rrule_string.

Coverage:
  - weekly count=4 → 4 occurrences exactly 7 days apart
  - until date inclusive (last occurrence ON or BEFORE until)
  - advance-window cap (occurrences beyond now+advance_days dropped silently)
  - >60 occurrences → ValueError
  - daily freq works
  - interval > 1 (biweekly)
  - exactly one of count/until required (validator)
  - build_rrule_string format
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("America/Toronto")


# Import will fail until recurrence.py is created — correct RED behaviour.
from app.services.recurrence import SeriesSpec, expand_series, build_rrule_string


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _future_dt(hour: int = 10, minute: int = 0, days_ahead: int = 1) -> datetime:
    """Return a tz-aware datetime in America/Toronto N days in the future."""
    d = datetime.now(TZ).date() + timedelta(days=days_ahead)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ)


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: SeriesSpec validator — exactly one of count/until required
# ─────────────────────────────────────────────────────────────────────────────

class TestSeriesSpecValidator:
    def test_count_only_is_valid(self):
        spec = SeriesSpec(freq="weekly", interval=1, count=4)
        assert spec.count == 4
        assert spec.until is None

    def test_until_only_is_valid(self):
        spec = SeriesSpec(freq="daily", until=date(2030, 1, 31))
        assert spec.until == date(2030, 1, 31)
        assert spec.count is None

    def test_both_count_and_until_raises(self):
        with pytest.raises(Exception):
            SeriesSpec(freq="weekly", count=4, until=date(2030, 1, 31))

    def test_neither_count_nor_until_raises(self):
        with pytest.raises(Exception):
            SeriesSpec(freq="weekly")

    def test_interval_must_be_positive(self):
        with pytest.raises(Exception):
            SeriesSpec(freq="weekly", count=4, interval=0)

    def test_interval_negative_raises(self):
        with pytest.raises(Exception):
            SeriesSpec(freq="weekly", count=4, interval=-1)


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: expand_series — core behaviour
# ─────────────────────────────────────────────────────────────────────────────

class TestExpandSeriesWeeklyCount:
    """weekly count=4 → exactly 4 occurrences, each 7 days apart."""

    def test_weekly_count_4_returns_4_occurrences(self):
        starts_at = _future_dt(10, 0, days_ahead=1)
        ends_at = starts_at + timedelta(hours=1)
        spec = SeriesSpec(freq="weekly", interval=1, count=4)
        occurrences, truncated = expand_series(starts_at, ends_at, spec, advance_days=90, tz=TZ)
        assert len(occurrences) == 4
        assert truncated is False

    def test_weekly_count_4_first_occurrence_equals_input(self):
        starts_at = _future_dt(10, 0, days_ahead=1)
        ends_at = starts_at + timedelta(hours=1)
        spec = SeriesSpec(freq="weekly", count=4)
        occurrences, _truncated = expand_series(starts_at, ends_at, spec, advance_days=90, tz=TZ)
        assert occurrences[0][0] == starts_at
        assert occurrences[0][1] == ends_at

    def test_weekly_count_4_occurrences_are_7_days_apart(self):
        starts_at = _future_dt(10, 0, days_ahead=1)
        ends_at = starts_at + timedelta(hours=1)
        spec = SeriesSpec(freq="weekly", count=4)
        occurrences, _truncated = expand_series(starts_at, ends_at, spec, advance_days=90, tz=TZ)
        for i in range(1, len(occurrences)):
            delta = occurrences[i][0] - occurrences[i - 1][0]
            assert delta == timedelta(weeks=1), (
                f"Expected 7-day gap between occurrences {i-1} and {i}, got {delta}"
            )

    def test_weekly_interval_2_occurrences_are_14_days_apart(self):
        starts_at = _future_dt(10, 0, days_ahead=1)
        ends_at = starts_at + timedelta(hours=1)
        spec = SeriesSpec(freq="weekly", interval=2, count=3)
        occurrences, _truncated = expand_series(starts_at, ends_at, spec, advance_days=90, tz=TZ)
        assert len(occurrences) == 3
        for i in range(1, len(occurrences)):
            delta = occurrences[i][0] - occurrences[i - 1][0]
            assert delta == timedelta(weeks=2), (
                f"Expected 14-day gap (interval=2), got {delta}"
            )


class TestExpandSeriesUntilInclusive:
    """until date is inclusive — last occurrence ON until must be included."""

    def test_until_inclusive_last_occurrence_on_until_date(self):
        # starts_at is on a Monday (or any day); until = starts_at + 3 weeks exactly
        starts_at = _future_dt(10, 0, days_ahead=1)
        ends_at = starts_at + timedelta(hours=1)
        until_date = (starts_at + timedelta(weeks=3)).date()
        spec = SeriesSpec(freq="weekly", until=until_date)
        occurrences, _truncated = expand_series(starts_at, ends_at, spec, advance_days=365, tz=TZ)
        # Should have 4 occurrences: week 0, 1, 2, 3 (all <= until)
        assert len(occurrences) == 4
        last_occ_date = occurrences[-1][0].astimezone(TZ).date()
        assert last_occ_date <= until_date

    def test_until_one_day_before_second_occurrence_gives_1_occurrence(self):
        starts_at = _future_dt(10, 0, days_ahead=1)
        ends_at = starts_at + timedelta(hours=1)
        # until = 6 days after starts_at (before the 2nd weekly occurrence)
        until_date = (starts_at + timedelta(days=6)).date()
        spec = SeriesSpec(freq="weekly", until=until_date)
        occurrences, _truncated = expand_series(starts_at, ends_at, spec, advance_days=365, tz=TZ)
        assert len(occurrences) == 1

    def test_until_exactly_on_second_occurrence_gives_2_occurrences(self):
        starts_at = _future_dt(10, 0, days_ahead=1)
        ends_at = starts_at + timedelta(hours=1)
        until_date = (starts_at + timedelta(weeks=1)).date()
        spec = SeriesSpec(freq="weekly", until=until_date)
        occurrences, _truncated = expand_series(starts_at, ends_at, spec, advance_days=365, tz=TZ)
        assert len(occurrences) == 2


class TestExpandSeriesAdvanceWindowCap:
    """Occurrences beyond now+advance_days are dropped silently."""

    def test_advance_days_caps_occurrences(self):
        # starts_at is 1 day in the future; advance_days=14 → only 2 weekly occurrences fit
        starts_at = _future_dt(10, 0, days_ahead=1)
        ends_at = starts_at + timedelta(hours=1)
        spec = SeriesSpec(freq="weekly", count=10)
        # advance_days=14: occurrences at +1d and +8d are within window; +15d is not
        occurrences, truncated = expand_series(starts_at, ends_at, spec, advance_days=14, tz=TZ)
        assert len(occurrences) == 2, (
            f"With advance_days=14 and starts_at +1d, expected 2 occurrences, got {len(occurrences)}"
        )
        assert truncated is True, "truncated must be True when advance-window capped the series"

    def test_all_occurrences_within_advance_window(self):
        starts_at = _future_dt(10, 0, days_ahead=1)
        ends_at = starts_at + timedelta(hours=1)
        spec = SeriesSpec(freq="weekly", count=8)
        advance_days = 30
        occurrences, _truncated = expand_series(starts_at, ends_at, spec, advance_days=advance_days, tz=TZ)
        cutoff = datetime.now(TZ) + timedelta(days=advance_days)
        for occ_start, _ in occurrences:
            assert occ_start <= cutoff, (
                f"Occurrence {occ_start} exceeds advance window cutoff {cutoff}"
            )


class TestExpandSeriesOver60ValueError:
    """>60 occurrences → ValueError raised."""

    def test_count_61_raises_value_error(self):
        starts_at = _future_dt(10, 0, days_ahead=1)
        ends_at = starts_at + timedelta(hours=1)
        spec = SeriesSpec(freq="daily", count=61)
        with pytest.raises(ValueError, match="60"):
            expand_series(starts_at, ends_at, spec, advance_days=365, tz=TZ)

    def test_count_60_does_not_raise(self):
        starts_at = _future_dt(10, 0, days_ahead=1)
        ends_at = starts_at + timedelta(hours=1)
        spec = SeriesSpec(freq="daily", count=60)
        # Should not raise; may be capped by advance window but no ValueError
        occurrences, _truncated = expand_series(starts_at, ends_at, spec, advance_days=365, tz=TZ)
        assert len(occurrences) <= 60

    def test_until_producing_over_60_raises_value_error(self):
        # daily for 61 days
        starts_at = _future_dt(10, 0, days_ahead=1)
        ends_at = starts_at + timedelta(hours=1)
        until_date = (starts_at + timedelta(days=61)).date()
        spec = SeriesSpec(freq="daily", until=until_date)
        with pytest.raises(ValueError, match="60"):
            expand_series(starts_at, ends_at, spec, advance_days=365, tz=TZ)


class TestExpandSeriesDaily:
    """Daily frequency works correctly."""

    def test_daily_count_3_returns_3_consecutive_days(self):
        starts_at = _future_dt(9, 0, days_ahead=1)
        ends_at = starts_at + timedelta(minutes=30)
        spec = SeriesSpec(freq="daily", count=3)
        occurrences, _truncated = expand_series(starts_at, ends_at, spec, advance_days=30, tz=TZ)
        assert len(occurrences) == 3
        for i in range(1, 3):
            delta = occurrences[i][0] - occurrences[i - 1][0]
            assert delta == timedelta(days=1), f"Expected 1-day gap, got {delta}"


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: build_rrule_string
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildRruleString:
    def test_weekly_count_produces_correct_string(self):
        spec = SeriesSpec(freq="weekly", interval=1, count=4)
        rrule = build_rrule_string(spec, until_fallback=date(2030, 12, 31))
        assert rrule.startswith("FREQ=WEEKLY")
        assert "INTERVAL=1" in rrule
        assert "COUNT=4" in rrule
        assert "UNTIL" not in rrule

    def test_daily_until_produces_correct_string(self):
        until = date(2030, 6, 15)
        spec = SeriesSpec(freq="daily", until=until)
        rrule = build_rrule_string(spec, until_fallback=date(2030, 12, 31))
        assert rrule.startswith("FREQ=DAILY")
        assert "INTERVAL=1" in rrule
        assert "UNTIL=20300615T235959Z" in rrule
        assert "COUNT" not in rrule

    def test_weekly_interval_2_count_produces_correct_string(self):
        spec = SeriesSpec(freq="weekly", interval=2, count=5)
        rrule = build_rrule_string(spec, until_fallback=date(2030, 12, 31))
        assert "FREQ=WEEKLY" in rrule
        assert "INTERVAL=2" in rrule
        assert "COUNT=5" in rrule

    def test_count_none_uses_until_fallback(self):
        """When spec.count is None and spec.until is also None (shouldn't happen via
        normal validator, but build_rrule_string must handle it gracefully via fallback)."""
        # This only tests the fallback path; normally until is set via until=
        spec = SeriesSpec(freq="daily", count=3)
        # Override spec.count to None manually to test fallback branch
        import copy
        s = copy.copy(spec)
        object.__setattr__(s, "count", None)
        object.__setattr__(s, "until", None)
        fallback = date(2030, 1, 1)
        rrule = build_rrule_string(s, until_fallback=fallback)
        assert "UNTIL=20300101T235959Z" in rrule
