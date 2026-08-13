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


def _month_bounds(month: str) -> tuple[date, date]:
    year_str, mon_str = month.split("-")
    year, mon = int(year_str), int(mon_str)
    first = date(year, mon, 1)
    last = date(year, mon, monthrange(year, mon)[1])
    return first, last


def _iso_week_start(day: date) -> date:
    """Monday of the ISO (Monday-start) week containing `day`."""
    return day - timedelta(days=day.weekday())


def _month_fixed_week_start(day: date) -> date:
    """1, 8, 15, 22 or 29 of `day`'s own month, whichever `day` falls in."""
    anchor_day = ((day.day - 1) // 7) * 7 + 1
    return day.replace(day=anchor_day)


def week_start_of(day: date, mode: str) -> date:
    """The start date of the week (under `mode`) that contains `day`."""
    _validate_mode(mode)
    if mode in _ISO_MODES:
        return _iso_week_start(day)
    return _month_fixed_week_start(day)


def owning_month(week_start: date, mode: str) -> str:
    """Which `'YYYY-MM'` month `week_start` (a week-start date) belongs to."""
    _validate_mode(mode)
    if mode == "iso_thursday":
        thursday = week_start + timedelta(days=3)
        return thursday.strftime("%Y-%m")
    if mode == "iso_first_day":
        week_end = week_start + timedelta(days=6)
        if (week_start.year, week_start.month) != (week_end.year, week_end.month):
            return week_end.strftime("%Y-%m")
        return week_start.strftime("%Y-%m")
    # month_fixed: weeks never straddle a boundary, so a week always
    # belongs to its own start month.
    return week_start.strftime("%Y-%m")


def weeks_of_month(month: str, mode: str) -> list[date]:
    """Ascending list of week-start dates that belong to `month` under `mode`."""
    _validate_mode(mode)
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
    scan_start = _iso_week_start(first) - timedelta(days=7)
    scan_end = _iso_week_start(last) + timedelta(days=7)
    weeks = []
    w = scan_start
    while w <= scan_end:
        if owning_month(w, mode) == month:
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


def shift_weeks(week_start: date, delta: int, mode: str) -> date:
    """`week_start` moved `delta` weeks forward (or back, if negative)."""
    _validate_mode(mode)
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


def week_label(week_start: date, mode: str) -> str:
    """Human-readable label, e.g. ``'2026-W32 · Aug 3-9'`` or
    ``'Aug W2 · Aug 8-14'`` (dash is an en dash)."""
    _validate_mode(mode)
    week_end = week_start + timedelta(days=6)
    date_range = _format_date_range(week_start, week_end)

    if mode in _ISO_MODES:
        iso_year, iso_week, _ = week_start.isocalendar()
        return f"{iso_year}-W{iso_week:02d} · {date_range}"

    month_str = week_start.strftime("%Y-%m")
    ordinal = weeks_of_month(month_str, "month_fixed").index(week_start) + 1
    return f"{week_start.strftime('%b')} W{ordinal} · {date_range}"
