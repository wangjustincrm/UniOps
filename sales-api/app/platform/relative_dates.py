"""Relative dates in a list's search (user 2026-11-26, #32): a date or date-time filter may name a period instead of a day.
Kept as its token in a saved view, so the view stays relative; resolved when the list is read, on the business day of the
platform clock (Toronto). A chip shows its words, never the token.

  today          the business day
  last_7_days    today and the 6 days before
  last_30_days   today and the 29 days before
  this_month     the month's first day up to today
  last_month     the whole month before
"""
from __future__ import annotations

from datetime import date, timedelta

RELATIVE: dict[str, str] = {"today": "Today", "last_7_days": "Last 7 days", "last_30_days": "Last 30 days",
                            "this_month": "This month", "last_month": "Last month"}
HELP = {"today": "The business day (Toronto).", "last_7_days": "Today and the 6 days before.",
        "last_30_days": "Today and the 29 days before.", "this_month": "From the 1st of this month up to today.",
        "last_month": "The whole month before this one."}


def is_relative(value: object) -> bool:
    return isinstance(value, str) and value in RELATIVE


def period(token: str, today: date) -> tuple[date, date]:
    """(first day, last day), both included."""
    if token == "today":
        return today, today
    if token == "last_7_days":
        return today - timedelta(days=6), today
    if token == "last_30_days":
        return today - timedelta(days=29), today
    first = today.replace(day=1)
    if token == "this_month":
        return first, today
    if token == "last_month":
        last = first - timedelta(days=1)
        return last.replace(day=1), last
    raise ValueError(f"not a relative date: {token!r}")


def as_meta() -> list[dict[str, str]]:
    return [{"value": k, "label": v, "help": HELP[k]} for k, v in RELATIVE.items()]
