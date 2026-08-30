"""Escalation thresholds.

The corrective-action ladder is the HSE Manager's schedule, so these cases are
written the way they stated it: reminder three days before, supervisor at five
days overdue, HSE Manager at ten.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.escalation import CapaLadder, capa_level, statutory_level

ET = ZoneInfo("America/Toronto")
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=ET)
LADDER = CapaLadder()
TODAY = date(2026, 8, 29)


# ── Statutory ───────────────────────────────────────────────────────────────

def test_a_deadline_days_away_is_not_escalated():
    assert statutory_level(NOW + timedelta(days=2), NOW) == 0


def test_inside_twenty_four_hours_warns():
    assert statutory_level(NOW + timedelta(hours=20), NOW) == 1


def test_inside_four_hours_is_urgent():
    assert statutory_level(NOW + timedelta(hours=3), NOW) == 2


def test_past_due_is_the_top_level():
    assert statutory_level(NOW - timedelta(minutes=1), NOW) == 3


def test_exactly_at_the_deadline_counts_as_past_due():
    """A deadline met exactly on the instant is not something to gamble on."""
    assert statutory_level(NOW, NOW) == 3


def test_the_boundaries_are_inclusive():
    assert statutory_level(NOW + timedelta(hours=24), NOW) == 1
    assert statutory_level(NOW + timedelta(hours=4), NOW) == 2


# ── Corrective actions ──────────────────────────────────────────────────────

def test_an_action_due_next_week_is_quiet():
    assert capa_level(TODAY + timedelta(days=7), TODAY, LADDER) == 0


def test_three_days_before_due_starts_reminding_the_owner():
    assert capa_level(TODAY + timedelta(days=3), TODAY, LADDER) == 1


def test_four_days_before_due_is_still_quiet():
    assert capa_level(TODAY + timedelta(days=4), TODAY, LADDER) == 0


def test_the_due_date_itself_is_level_one():
    assert capa_level(TODAY, TODAY, LADDER) == 1


def test_five_days_overdue_brings_in_the_supervisor():
    assert capa_level(TODAY - timedelta(days=5), TODAY, LADDER) == 2


def test_four_days_overdue_is_still_only_the_owner():
    assert capa_level(TODAY - timedelta(days=4), TODAY, LADDER) == 1


def test_ten_days_overdue_reaches_the_hse_manager():
    assert capa_level(TODAY - timedelta(days=10), TODAY, LADDER) == 3


def test_it_stops_at_the_hse_manager():
    """The HSE Manager asked for the ladder to stop here rather than climb to
    senior management."""
    assert capa_level(TODAY - timedelta(days=90), TODAY, LADDER) == 3


def test_the_thresholds_come_from_configuration():
    strict = CapaLadder(remind_before_days=1, supervisor_days=2, manager_days=3)
    assert capa_level(TODAY + timedelta(days=2), TODAY, strict) == 0
    assert capa_level(TODAY + timedelta(days=1), TODAY, strict) == 1
    assert capa_level(TODAY - timedelta(days=2), TODAY, strict) == 2
    assert capa_level(TODAY - timedelta(days=3), TODAY, strict) == 3
