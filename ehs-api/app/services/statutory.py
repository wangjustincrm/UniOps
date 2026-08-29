"""Statutory deadline arithmetic.

Every function here is pure: it takes the moment a clock started plus the
holiday list, and returns the moment it expires. Nothing reads the wall clock
or the database. That is the whole point — these are the calculations that must
not be wrong, and a pure function is one you can put a regulation's own wording
into a test case for.

The deadlines are computed once, when the clock starts, and stored as absolute
timestamps on `ehs_statutory_deadlines`. They are deliberately not expressed as
a generated column: the business-day rules have to skip Ontario statutory
holidays, which Postgres knows nothing about, while the 48-hour rule counts
wall-clock hours straight through a weekend. One SQL expression covering both
would be unmaintainable and untestable.

Business-day arithmetic runs in Ontario local time, because which *day* a
moment falls on depends on the local date. An injury reported at 21:00 EDT on a
Friday is 01:00 UTC Saturday — counting business days from the UTC date would
lose a day.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ONTARIO = ZoneInfo("America/Toronto")

# ── The rules, with the authority each one comes from ────────────────────────
# Kept as module constants so a test can cite the section it is checking, and
# so changing one is a visible, reviewable edit rather than a buried literal.

MOL_WRITTEN_REPORT_HOURS = 48
MOL_WRITTEN_REPORT_AUTHORITY = "OHSA s.51(1)"
"""Fatality or critical injury: written report to a Director of the Ministry
'within forty-eight hours after the occurrence'. Wall-clock hours from the
occurrence — not from when the employer found out, and not business hours."""

WSIB_FORM7_BUSINESS_DAYS = 3
WSIB_FORM7_AUTHORITY = "WSIA / WSIB Form 7"
"""The WSIB must RECEIVE the employer's report within three business days of
the employer learning of the obligation. Note both halves: the clock starts at
employer awareness, not at the occurrence, and it ends at receipt, not at
despatch."""

JHSC_REPLY_CALENDAR_DAYS = 21
JHSC_REPLY_AUTHORITY = "OHSA s.9(20)"
"""Employer response to a written JHSC recommendation, within twenty-one days.
Calendar days."""

POLICY_REVIEW_MONTHS = 12
POLICY_REVIEW_AUTHORITY = "OHSA s.25(2)(j)"

SDS_REVIEW_YEARS = 3
SDS_REVIEW_AUTHORITY = "Reg 860 (WHMIS)"

KIND_CALENDAR = "calendar"
KIND_BUSINESS = "business"

# kind -> (clock_type, regulation_ref)
RULES: dict[str, tuple[str, str]] = {
    "mol_48h": (KIND_CALENDAR, MOL_WRITTEN_REPORT_AUTHORITY),
    "wsib_form7": (KIND_BUSINESS, WSIB_FORM7_AUTHORITY),
    "jhsc_reply_21d": (KIND_CALENDAR, JHSC_REPLY_AUTHORITY),
    "policy_annual": (KIND_CALENDAR, POLICY_REVIEW_AUTHORITY),
    "sds_3y": (KIND_CALENDAR, SDS_REVIEW_AUTHORITY),
    "cert_expiry": (KIND_CALENDAR, ""),
}


class UnknownDeadlineKind(ValueError):
    """Raised rather than guessing — a mistyped kind must not silently get a
    plausible-looking deadline."""


def is_business_day(day: date, holidays: frozenset[date] | set[date]) -> bool:
    """Monday–Friday, excluding Ontario statutory holidays."""
    return day.weekday() < 5 and day not in holidays


def add_business_days(
    start: datetime, days: int, holidays: frozenset[date] | set[date]
) -> datetime:
    """`days` business days after `start`, expiring at end of that day.

    The starting day is never counted, whether or not it is itself a business
    day: "within three business days after the employer learns" gives the
    employer three whole working days, so an event on Friday afternoon is due
    at the end of the following Wednesday, not Tuesday.

    Returns the last instant of the target day in Ontario local time, converted
    back to UTC. End-of-day because the obligation is discharged by filing at
    any point during that day.
    """
    if days < 1:
        raise ValueError("business-day deadlines must be at least one day")
    local_day = start.astimezone(ONTARIO).date()
    counted = 0
    while counted < days:
        local_day += timedelta(days=1)
        if is_business_day(local_day, holidays):
            counted += 1
    end_of_day = datetime.combine(local_day, time(23, 59, 59), tzinfo=ONTARIO)
    return end_of_day.astimezone(start.tzinfo or ONTARIO)


def add_calendar_years(start: datetime, years: int) -> datetime:
    """Same date `years` later; 29 February lands on 28 February."""
    local = start.astimezone(ONTARIO)
    try:
        moved = local.replace(year=local.year + years)
    except ValueError:  # 29 Feb in a non-leap target year
        moved = local.replace(year=local.year + years, day=28)
    return moved.astimezone(start.tzinfo or ONTARIO)


def compute_due(
    kind: str,
    starts_at: datetime,
    holidays: frozenset[date] | set[date] | None = None,
) -> datetime:
    """When the clock that just started expires.

    `starts_at` must be timezone-aware; a naive datetime is ambiguous about
    which day it falls on and this module refuses to guess.
    """
    if starts_at.tzinfo is None:
        raise ValueError("starts_at must be timezone-aware")
    holidays = holidays or frozenset()

    if kind == "mol_48h":
        # Wall-clock hours. Runs straight through weekends and holidays — a
        # critical injury on Saturday evening is still due Monday evening.
        return starts_at + timedelta(hours=MOL_WRITTEN_REPORT_HOURS)

    if kind == "wsib_form7":
        return add_business_days(starts_at, WSIB_FORM7_BUSINESS_DAYS, holidays)

    if kind == "jhsc_reply_21d":
        return starts_at + timedelta(days=JHSC_REPLY_CALENDAR_DAYS)

    if kind == "policy_annual":
        return add_calendar_years(starts_at, 1)

    if kind == "sds_3y":
        return add_calendar_years(starts_at, SDS_REVIEW_YEARS)

    raise UnknownDeadlineKind(
        f"no rule for deadline kind {kind!r}; known kinds: {sorted(RULES)}"
    )


def internal_target(kind: str, due_at: datetime, holidays: frozenset[date] | set[date] | None = None) -> datetime:
    """The date the module chases, which is not always the legal date.

    WSIB's limit is on *receipt*, so filing on the legal deadline is already
    too late if anything goes wrong in transit. The module therefore shows one
    business day earlier as its own target. Every other clock targets its own
    deadline.
    """
    if kind != "wsib_form7":
        return due_at
    holidays = holidays or frozenset()
    local_day = due_at.astimezone(ONTARIO).date()
    while True:
        local_day -= timedelta(days=1)
        if is_business_day(local_day, holidays):
            break
    end_of_day = datetime.combine(local_day, time(23, 59, 59), tzinfo=ONTARIO)
    return end_of_day.astimezone(due_at.tzinfo or ONTARIO)
