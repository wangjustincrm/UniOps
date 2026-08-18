"""Week calendar — pure functions, no DB, no clock.

**This module is the only place in the codebase allowed to know the
difference between the three week definitions below.** The scheduling
engine, the API and the export layer only ever consume a list of week-start
dates (or a single week-start date) produced by this module; they must never
branch on `mode` themselves. If mode-specific logic ever needs to leak
outside `week_calendar.py`, that is a design smell — bring the new need back
here instead.

Three configurable modes (`WEEK_MODES`):

- ``iso_thursday`` (default) — Monday-start ISO weeks. A week that straddles
  a month boundary belongs to whichever month contains its Thursday (the
  ISO 8601 rule for which year/week-number a week belongs to, applied to
  months instead of years).
- ``iso_first_day`` — Monday-start ISO weeks, same week boundaries as
  ``iso_thursday``. A straddling week instead belongs to the *newer* month
  whenever that week contains that month's first day; a week that does not
  straddle a boundary is unambiguous and belongs to its own month either
  way.
- ``month_fixed`` — weeks start on the 1st of each month and step by 7 days
  (1, 8, 15, 22, 29, ...). Weeks never straddle a month boundary under this
  mode; the last "week" of a month is a 1-7 day remainder, and because
  weeks are not always 7 days long here, `shift_weeks` cannot use plain
  `date + timedelta(weeks=n)` arithmetic — it must walk the real week
  sequence one step at a time, crossing month boundaries by switching to
  the neighbouring month's own sequence.

**Week start day is a second, orthogonal dimension.** Every public function
takes a keyword-only `start_dow` (0=Monday .. 6=Sunday, default 0) saying
which weekday a week begins on -- this factory's week runs Saturday to
Friday, so it plans on `start_dow=5`. It applies to the two ISO-style modes
only: `month_fixed` weeks begin on the 1st of the month by definition and
are unaffected (the argument is still range-checked there, because passing
a bad value is a caller bug either way).

Under a non-Monday start the `iso_thursday` rule reads as **"the month
holding the week's FOURTH day"** -- with Monday-start weeks that day is the
Thursday the mode is named after, so nothing changes for existing plans; a
Saturday-start week's fourth day is its Tuesday. The rule was always "which
month holds most of this week", and the fourth day is where that tips.

An unrecognised `mode` always raises `ValueError`. It must never silently
fall back to a default — a typo'd configuration value silently reverting to
`iso_thursday` would change how the entire factory's plan is bucketed into
weeks with no signal to anyone that it happened.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

WEEK_MODES = ("iso_thursday", "iso_first_day", "month_fixed")

_ISO_MODES = ("iso_thursday", "iso_first_day")


def _validate_mode(mode: str) -> None:
    if mode not in WEEK_MODES:
        raise ValueError(
            f"Unknown week mode {mode!r}; must be one of {WEEK_MODES}. "
            "Refusing to silently fall back to a default."
        )


def _validate_start_dow(start_dow: int) -> None:
    """`start_dow` must be a plain int 0..6. `bool` is rejected explicitly:
    Python makes `True == 1`, so a stray boolean would silently plan the
    whole factory on Tuesday-start weeks."""
    if isinstance(start_dow, bool) or not isinstance(start_dow, int) or not 0 <= start_dow <= 6:
        raise ValueError(
            f"start_dow must be an int 0..6 (0=Monday), got {start_dow!r}. "
            "Refusing to silently fall back to Monday."
        )


def _month_bounds(month: str) -> tuple[date, date]:
    year_str, mon_str = month.split("-")
    year, mon = int(year_str), int(mon_str)
    first = date(year, mon, 1)
    last = date(year, mon, monthrange(year, mon)[1])
    return first, last


def _iso_week_start(day: date, start_dow: int = 0) -> date:
    """Start of the week containing `day` when weeks begin on `start_dow`
    (0=Monday, the ISO default)."""
    return day - timedelta(days=(day.weekday() - start_dow) % 7)


def _month_fixed_week_start(day: date) -> date:
    """1, 8, 15, 22 or 29 of `day`'s own month, whichever `day` falls in."""
    anchor_day = ((day.day - 1) // 7) * 7 + 1
    return day.replace(day=anchor_day)


def week_start_of(day: date, mode: str, *, start_dow: int = 0) -> date:
    """The start date of the week (under `mode`) that contains `day`."""
    _validate_mode(mode)
    _validate_start_dow(start_dow)
    if mode in _ISO_MODES:
        return _iso_week_start(day, start_dow)
    return _month_fixed_week_start(day)


def owning_month(week_start: date, mode: str, *, start_dow: int = 0) -> str:
    """Which `'YYYY-MM'` month `week_start` (a week-start date) belongs to."""
    _validate_mode(mode)
    _validate_start_dow(start_dow)
    if mode == "iso_thursday":
        # The week's FOURTH day -- the Thursday of a Monday-start week, the
        # Tuesday of a Saturday-start one. See this module's docstring.
        fourth_day = week_start + timedelta(days=3)
        return fourth_day.strftime("%Y-%m")
    if mode == "iso_first_day":
        week_end = week_start + timedelta(days=6)
        if (week_start.year, week_start.month) != (week_end.year, week_end.month):
            return week_end.strftime("%Y-%m")
        return week_start.strftime("%Y-%m")
    # month_fixed: weeks never straddle a boundary, so a week always
    # belongs to its own start month.
    return week_start.strftime("%Y-%m")


def weeks_of_month(month: str, mode: str, *, start_dow: int = 0) -> list[date]:
    """Ascending list of week-start dates that belong to `month` under `mode`."""
    _validate_mode(mode)
    _validate_start_dow(start_dow)
    first, last = _month_bounds(month)

    if mode == "month_fixed":
        weeks = []
        d = first
        while d <= last:
            weeks.append(d)
            d += timedelta(days=7)
        return weeks

    # iso_thursday / iso_first_day: scan a window one week wider on each
    # side of the calendar month (enough to catch any straddling week) and
    # keep whichever weeks `owning_month` actually assigns to `month`.
    scan_start = _iso_week_start(first, start_dow) - timedelta(days=7)
    scan_end = _iso_week_start(last, start_dow) + timedelta(days=7)
    weeks = []
    w = scan_start
    while w <= scan_end:
        if owning_month(w, mode, start_dow=start_dow) == month:
            weeks.append(w)
        w += timedelta(days=7)
    return weeks


def _month_fixed_step(week_start: date, step: int) -> date:
    """One single step (+1 or -1) along the real month_fixed week sequence."""
    month_str = week_start.strftime("%Y-%m")
    weeks = weeks_of_month(month_str, "month_fixed")
    idx = weeks.index(week_start) + step
    if 0 <= idx < len(weeks):
        return weeks[idx]

    year, mon = week_start.year, week_start.month
    if step > 0:
        neighbour_year, neighbour_mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
        neighbour = f"{neighbour_year:04d}-{neighbour_mon:02d}"
        return weeks_of_month(neighbour, "month_fixed")[0]
    else:
        neighbour_year, neighbour_mon = (year - 1, 12) if mon == 1 else (year, mon - 1)
        neighbour = f"{neighbour_year:04d}-{neighbour_mon:02d}"
        return weeks_of_month(neighbour, "month_fixed")[-1]


def shift_weeks(week_start: date, delta: int, mode: str, *, start_dow: int = 0) -> date:
    """`week_start` moved `delta` weeks forward (or back, if negative).

    `start_dow` does not change the arithmetic under the ISO modes (every
    week is 7 days long whichever day it starts on); it is accepted and
    range-checked so every entry point of this module has one signature."""
    _validate_mode(mode)
    _validate_start_dow(start_dow)
    if mode in _ISO_MODES:
        return week_start + timedelta(weeks=delta)

    # month_fixed: week lengths are not uniformly 7 days (the last week of
    # a month is a short remainder), so plain `timedelta(weeks=n)`
    # arithmetic would land off the real week grid. Walk the actual
    # sequence one step at a time instead.
    step = 1 if delta >= 0 else -1
    current = week_start
    for _ in range(abs(delta)):
        current = _month_fixed_step(current, step)
    return current


def _short_month(month: str) -> str:
    """``'2026-08'`` -> ``'Aug'`` -- the short month name `week_label` prints
    for a week whose owning month may differ from its start date's month."""
    year, mon = month.split("-")
    return date(int(year), int(mon), 1).strftime("%b")


_EN_DASH = "–"


def _format_date_range(start: date, end: date) -> str:
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.strftime('%b')} {start.day}{_EN_DASH}{end.day}"
    if start.year == end.year:
        return (
            f"{start.strftime('%b')} {start.day}{_EN_DASH}"
            f"{end.strftime('%b')} {end.day}"
        )
    return (
        f"{start.strftime('%b')} {start.day}, {start.year}{_EN_DASH}"
        f"{end.strftime('%b')} {end.day}, {end.year}"
    )


def week_of_year(week_start: date, *, start_dow: int = 0) -> tuple[int, int]:
    """``(year, week number)`` for a week, whatever weekday it starts on.

    Taken from the ISO week of the week's **fourth day** — the same anchor
    `owning_month` uses to decide which month a week belongs to, so a plan
    cannot say a week is in September while numbering it as an August one.

    ISO numbering is Monday-based, so a Saturday-start week cannot simply be
    asked for its own ISO number: `week_start.isocalendar()` would name the
    Monday-based week that *contains that Saturday*, which is a different seven
    days from the one being labelled. Anchoring on the fourth day fixes that,
    because the fourth day is always in the majority half of the week.

    Two properties, both pinned by tests:

    - **On a Monday start this is exactly ISO.** The fourth day is the
      Thursday, and a Monday-start week's ISO number is defined by its
      Thursday — so nothing about existing Monday-based labelling changes.
    - **Across a Saturday-start year the numbers are unique and consecutive**
      (52 weeks, 52 numbers, no jumps in 2026), because consecutive weeks'
      anchors are exactly seven days apart.
    """
    _validate_start_dow(start_dow)
    anchor = week_start + timedelta(days=3)
    iso_year, iso_week, _ = anchor.isocalendar()
    return iso_year, iso_week


def week_label(week_start: date, mode: str, *, start_dow: int = 0) -> str:
    """Human-readable label, e.g. ``'2026-W32 · Aug 3-9'`` (dash is an en dash).

    The week number is the YEAR's, not the month's: planning, the factory floor
    and the ERP all talk in week numbers that run 1..52, and a label reading
    "Sep W1" forces everyone to translate. See `week_of_year` for how it is
    derived on a week that does not start on a Monday.

    `month_fixed` keeps a within-month ordinal, because under that mode a
    "week" is a fixed slice of one month rather than a real seven-day
    calendar week — numbering those across a year would invent a sequence the
    calendar does not have."""
    _validate_mode(mode)
    _validate_start_dow(start_dow)
    week_end = week_start + timedelta(days=6)
    date_range = _format_date_range(week_start, week_end)

    if mode in _ISO_MODES:
        iso_year, iso_week = week_of_year(week_start, start_dow=start_dow)
        return f"{iso_year}-W{iso_week:02d} · {date_range}"

    month_str = week_start.strftime("%Y-%m")
    ordinal = weeks_of_month(month_str, "month_fixed").index(week_start) + 1
    return f"{week_start.strftime('%b')} W{ordinal} · {date_range}"
