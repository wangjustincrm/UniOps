"""Tests for the statutory deadline rules.

Each case cites the authority it is checking. These dates are hardcoded on
purpose: the subject under test is calendar arithmetic against named
regulations, so a fixture computed relative to "now" would test nothing and
would drift into passing for the wrong reason.

Ontario statutory holidays used below are the real ones — the point of several
cases is that a deadline lands differently because of them.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.statutory import (
    JHSC_REPLY_AUTHORITY,
    MOL_WRITTEN_REPORT_AUTHORITY,
    WSIB_FORM7_AUTHORITY,
    UnknownDeadlineKind,
    add_business_days,
    compute_due,
    internal_target,
    is_business_day,
)

ET = ZoneInfo("America/Toronto")
UTC = ZoneInfo("UTC")

# Ontario statutory holidays, 2026.
HOLIDAYS_2026 = frozenset({
    date(2026, 1, 1),    # New Year's Day
    date(2026, 2, 16),   # Family Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 18),   # Victoria Day
    date(2026, 7, 1),    # Canada Day
    date(2026, 9, 7),    # Labour Day
    date(2026, 10, 12),  # Thanksgiving
    date(2026, 12, 25),  # Christmas Day
    date(2026, 12, 26),  # Boxing Day
})
HOLIDAYS_2027 = frozenset({date(2027, 1, 1)})
HOLIDAYS = HOLIDAYS_2026 | HOLIDAYS_2027


# ── OHSA s.51(1): 48 hours after the occurrence ─────────────────────────────

def test_mol_48h_is_wall_clock_from_the_occurrence():
    """OHSA s.51(1): 'within forty-eight hours after the occurrence'."""
    occurred = datetime(2026, 8, 28, 22, 10, tzinfo=ET)
    assert compute_due("mol_48h", occurred, HOLIDAYS) == datetime(2026, 8, 30, 22, 10, tzinfo=ET)


def test_mol_48h_runs_straight_through_a_weekend():
    """The 48 hours are wall-clock, not business hours — a Friday evening
    critical injury is due Sunday evening, weekend or not."""
    occurred = datetime(2026, 8, 28, 18, 0, tzinfo=ET)  # Friday
    due = compute_due("mol_48h", occurred, HOLIDAYS)
    assert due == datetime(2026, 8, 30, 18, 0, tzinfo=ET)  # Sunday
    assert due.weekday() == 6


def test_mol_48h_runs_through_a_statutory_holiday():
    occurred = datetime(2026, 12, 24, 9, 0, tzinfo=ET)  # Christmas Eve
    assert compute_due("mol_48h", occurred, HOLIDAYS) == datetime(2026, 12, 26, 9, 0, tzinfo=ET)


def test_mol_48h_crosses_a_month_boundary():
    occurred = datetime(2026, 6, 29, 23, 30, tzinfo=ET)
    assert compute_due("mol_48h", occurred, HOLIDAYS) == datetime(2026, 7, 1, 23, 30, tzinfo=ET)


def test_mol_48h_ignores_the_holiday_list_entirely():
    """Passing holidays must not change a wall-clock result."""
    occurred = datetime(2026, 12, 24, 9, 0, tzinfo=ET)
    assert compute_due("mol_48h", occurred, HOLIDAYS) == compute_due("mol_48h", occurred, frozenset())


# ── WSIB: three business days from employer awareness, to receipt ───────────

def test_wsib_three_business_days_midweek():
    """Monday awareness gives Tuesday, Wednesday, Thursday."""
    aware = datetime(2026, 8, 24, 10, 0, tzinfo=ET)  # Monday
    due = compute_due("wsib_form7", aware, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 8, 27)  # Thursday
    assert due.astimezone(ET).hour == 23


def test_wsib_skips_the_weekend():
    """Friday awareness: Monday, Tuesday, Wednesday are the three days."""
    aware = datetime(2026, 8, 28, 14, 0, tzinfo=ET)  # Friday
    due = compute_due("wsib_form7", aware, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 9, 2)  # Wednesday


def test_wsib_skips_a_statutory_holiday():
    """Labour Day (7 Sep 2026) is not a business day, pushing the deadline out
    by one. Without the holiday list this would land on Wednesday 9 Sep."""
    aware = datetime(2026, 9, 4, 9, 0, tzinfo=ET)  # Friday before Labour Day
    with_holiday = compute_due("wsib_form7", aware, HOLIDAYS)
    without_holiday = compute_due("wsib_form7", aware, frozenset())
    assert with_holiday.astimezone(ET).date() == date(2026, 9, 10)  # Thursday
    assert without_holiday.astimezone(ET).date() == date(2026, 9, 9)
    assert with_holiday > without_holiday


def test_wsib_crosses_the_new_year_and_two_holidays():
    """Christmas Day and Boxing Day 2026 fall Friday/Saturday, and New Year's
    Day 2027 is a Friday. Awareness on Christmas Eve lands well into January."""
    aware = datetime(2026, 12, 24, 16, 0, tzinfo=ET)  # Thursday
    due = compute_due("wsib_form7", aware, HOLIDAYS)
    # 25th holiday, 26th–27th weekend, 28th–30th are the three business days.
    assert due.astimezone(ET).date() == date(2026, 12, 30)


def test_wsib_starting_day_is_never_counted():
    """'Within three business days after' gives three whole working days, so a
    Tuesday-morning and a Tuesday-late-evening awareness share a deadline."""
    early = datetime(2026, 8, 25, 6, 0, tzinfo=ET)
    late = datetime(2026, 8, 25, 23, 0, tzinfo=ET)
    assert compute_due("wsib_form7", early, HOLIDAYS) == compute_due("wsib_form7", late, HOLIDAYS)


def test_wsib_awareness_on_a_weekend_still_gets_three_working_days():
    aware = datetime(2026, 8, 29, 11, 0, tzinfo=ET)  # Saturday
    due = compute_due("wsib_form7", aware, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 9, 2)  # Mon, Tue, Wed


def test_wsib_deadline_is_end_of_the_third_day():
    """The obligation is discharged by filing at any point during that day."""
    aware = datetime(2026, 8, 24, 10, 0, tzinfo=ET)
    due = compute_due("wsib_form7", aware, HOLIDAYS).astimezone(ET)
    assert (due.hour, due.minute, due.second) == (23, 59, 59)


# ── The timezone trap ───────────────────────────────────────────────────────

def test_business_days_count_from_the_ontario_date_not_the_utc_date():
    """21:00 Friday in Ontario is 01:00 Saturday UTC. Counting from the UTC
    date would start a day late and hand back a deadline a day too generous."""
    aware_utc = datetime(2026, 8, 29, 1, 0, tzinfo=UTC)  # = Fri 28th 21:00 ET
    assert aware_utc.astimezone(ET).date() == date(2026, 8, 28)
    due = compute_due("wsib_form7", aware_utc, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 9, 2)


def test_naive_datetimes_are_refused():
    """A naive timestamp cannot say which local day it falls on, and guessing
    would silently move a legal deadline."""
    with pytest.raises(ValueError):
        compute_due("wsib_form7", datetime(2026, 8, 24, 10, 0), HOLIDAYS)


# ── OHSA s.9(20): 21 calendar days ──────────────────────────────────────────

def test_jhsc_reply_is_twenty_one_calendar_days():
    recommended = datetime(2026, 8, 21, 9, 0, tzinfo=ET)
    assert compute_due("jhsc_reply_21d", recommended, HOLIDAYS) == datetime(2026, 9, 11, 9, 0, tzinfo=ET)


def test_jhsc_reply_does_not_skip_weekends_or_holidays():
    recommended = datetime(2026, 12, 10, 9, 0, tzinfo=ET)
    due = compute_due("jhsc_reply_21d", recommended, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 12, 31)


# ── Annual and multi-year reviews ───────────────────────────────────────────

def test_policy_review_is_one_year_later():
    reviewed = datetime(2026, 3, 15, 12, 0, tzinfo=ET)
    assert compute_due("policy_annual", reviewed, HOLIDAYS).astimezone(ET).date() == date(2027, 3, 15)


def test_sds_review_is_three_years_later():
    issued = datetime(2026, 5, 4, 12, 0, tzinfo=ET)
    assert compute_due("sds_3y", issued, HOLIDAYS).astimezone(ET).date() == date(2029, 5, 4)


def test_leap_day_review_lands_on_the_28th():
    """29 February has no counterpart in a non-leap year; falling back to the
    28th keeps the review inside the compliant year rather than slipping to
    1 March."""
    reviewed = datetime(2028, 2, 29, 12, 0, tzinfo=ET)
    assert compute_due("policy_annual", reviewed, HOLIDAYS).astimezone(ET).date() == date(2029, 2, 28)


# ── Internal target: WSIB is a receipt deadline ─────────────────────────────

def test_internal_target_for_wsib_is_one_business_day_earlier():
    aware = datetime(2026, 8, 24, 10, 0, tzinfo=ET)  # Monday
    due = compute_due("wsib_form7", aware, HOLIDAYS)
    target = internal_target("wsib_form7", due, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 8, 27)
    assert target.astimezone(ET).date() == date(2026, 8, 26)


def test_internal_target_skips_back_over_a_weekend():
    aware = datetime(2026, 8, 27, 10, 0, tzinfo=ET)  # Thursday -> due Tuesday
    due = compute_due("wsib_form7", aware, HOLIDAYS)
    assert due.astimezone(ET).date() == date(2026, 9, 1)
    assert internal_target("wsib_form7", due, HOLIDAYS).astimezone(ET).date() == date(2026, 8, 31)


def test_internal_target_leaves_other_clocks_alone():
    occurred = datetime(2026, 8, 28, 22, 10, tzinfo=ET)
    due = compute_due("mol_48h", occurred, HOLIDAYS)
    assert internal_target("mol_48h", due, HOLIDAYS) == due


# ── Guardrails ──────────────────────────────────────────────────────────────

def test_unknown_kind_raises_rather_than_guessing():
    with pytest.raises(UnknownDeadlineKind):
        compute_due("mol_24h", datetime(2026, 8, 24, 10, 0, tzinfo=ET), HOLIDAYS)


def test_is_business_day():
    assert is_business_day(date(2026, 8, 27), HOLIDAYS)          # Thursday
    assert not is_business_day(date(2026, 8, 29), HOLIDAYS)      # Saturday
    assert not is_business_day(date(2026, 9, 7), HOLIDAYS)       # Labour Day
    assert is_business_day(date(2026, 9, 7), frozenset())        # without the list


def test_add_business_days_requires_at_least_one():
    with pytest.raises(ValueError):
        add_business_days(datetime(2026, 8, 24, 10, 0, tzinfo=ET), 0, HOLIDAYS)


def test_authorities_are_stated_on_the_rules():
    """The citation travels with the rule so a screen can show it and a
    reviewer can check it."""
    assert "51(1)" in MOL_WRITTEN_REPORT_AUTHORITY
    assert "WSIB" in WSIB_FORM7_AUTHORITY
    assert "9(20)" in JHSC_REPLY_AUTHORITY


def test_a_full_year_of_wsib_deadlines_never_lands_on_a_non_business_day():
    """Property check across every day of 2026: the deadline is always a
    working day, never a weekend or a statutory holiday."""
    day = datetime(2026, 1, 1, 10, 0, tzinfo=ET)
    while day.year == 2026:
        due = compute_due("wsib_form7", day, HOLIDAYS).astimezone(ET).date()
        assert is_business_day(due, HOLIDAYS), f"{day.date()} -> {due} is not a business day"
        assert due > day.date()
        day += timedelta(days=1)


async def test_days_since_counts_injuries_not_every_record(hse_manager, worker, test_engine):
    """Reporting a good catch must not reset the tile to zero.

    It counted every incident of any kind, so a near miss — the thing the
    module most wants people to report — wiped the number that the plant reads
    as "how we are doing". HSE asked for it to mean medical incidents, and to
    say so on the tile.
    """
    from datetime import datetime, timedelta, timezone

    _, client = hse_manager
    injured, _ = worker
    long_ago = datetime.now(timezone.utc) - timedelta(days=40)

    await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Cut finger",
        "incident_type": "first_aid",
        "occurred_at": long_ago.isoformat(),
        "description": "Caught on a burr.",
        "injured_user_id": str(injured.id),
    })
    before = (await client.get("/api/v1/dashboard")).json()["headline"]
    assert before["days_since_last_incident"] >= 39

    # A near miss today. Good news, and it used to read as a reset.
    await client.post("/api/v1/incidents", json={
        "form_kind": "near_miss", "title": "Pallet nearly fell",
        "incident_type": "near_miss",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "description": "Nobody underneath.",
        "talked_to_operator": "yes",
    })
    after = (await client.get("/api/v1/dashboard")).json()["headline"]
    assert after["days_since_last_incident"] == before["days_since_last_incident"], (
        "a near miss is not an injury, and reporting one must not cost the plant "
        "its count")


async def test_changing_the_check_cycle_moves_the_next_check(hse_manager):
    """A shorter cycle that does not bring the date forward is cosmetic.

    An extinguisher moved from quarterly to monthly kept its date three months
    out and quietly missed two checks — the register said monthly, the schedule
    kept the old promise, and nothing on screen disagreed.
    """
    from datetime import date

    _, client = hse_manager
    created = await client.post("/api/v1/safety-assets", json={
        "name": "Extinguisher — ABC 10lb",
        "inspection_frequency": "quarterly",
    })
    assert created.status_code in (200, 201), created.text
    quarterly_due = date.fromisoformat(created.json()["next_due_on"])

    tightened = await client.patch(f"/api/v1/safety-assets/{created.json()['id']}", json={
        "inspection_frequency": "monthly",
    })
    assert tightened.status_code == 200, tightened.text
    monthly_due = date.fromisoformat(tightened.json()["next_due_on"])
    assert monthly_due < quarterly_due, (
        "monthly must fall due sooner than the quarterly date it replaced")

    # And taken off a cycle, nothing is owed at all.
    off = await client.patch(f"/api/v1/safety-assets/{created.json()['id']}", json={
        "inspection_frequency": None,
    })
    assert off.json()["next_due_on"] is None, (
        "an item nobody schedules must stop raising checks")


async def test_checking_the_same_item_twice_in_a_day_is_refused_not_a_crash(hse_manager):
    """One row per asset per day is a database constraint.

    A second check on the same item on the same day cannot be a second row,
    and inserting one answered 500 — which is what the third acceptance round
    hit: "internal server error on pass or fail entries". It is also what
    happens on the ordinary path, because the sweep raises a row for anything
    due today and the ad-hoc check then tried to make another.
    """
    _, client = hse_manager
    asset = (await client.post("/api/v1/safety-assets", json={
        "name": "Eyewash — packaging line", "inspection_frequency": "monthly",
    })).json()

    first = await client.post(f"/api/v1/safety-assets/{asset['id']}/inspections",
                              json={"result": "passed", "notes": "Flushed, clear."})
    assert first.status_code in (200, 201), first.text
    assert first.json()["result"] == "passed"
    assert first.json()["performed_by_name"], "a check has to say who did it"

    again = await client.post(f"/api/v1/safety-assets/{asset['id']}/inspections",
                              json={"result": "failed", "notes": "Second look."})
    assert again.status_code == 409, (
        f"expected a plain refusal, got {again.status_code}: {again.text}")
    assert "already checked" in again.json()["detail"]
    assert asset["asset_no"] in again.json()["detail"], (
        "the message has to name the item — somebody checking a row of "
        "extinguishers needs to know which one it means")

    # And the first result stands: a recorded result is the register's
    # evidence and is not replaced by a later attempt.
    checks = (await client.get(
        f"/api/v1/safety-assets/inspections?asset_id={asset['id']}")).json()
    done = [c for c in checks if c["performed_on"]]
    assert len(done) == 1 and done[0]["result"] == "passed"
