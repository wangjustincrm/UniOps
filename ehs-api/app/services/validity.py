"""When a training record expires, from the course it is for.

★ Round 9, question 5: "each course should carry its own expiry date. … if I
create a course with a 12 month expiry, have the certificate expire in 12
months minus a day." Calendar months, not thirty-day ones: the approximation
put a 36-month course's expiry 18 days early.

A month that has no such day: the date rolls into the next month's first,
and the day before that is the month's last — round 8's own example, "29
February plus three years falls on 1 March; less a day is 28 February". So
31 January plus one month expires on the last day of February. (★ Review,
uniops-8b: it used to clamp to the last day and then take a day off, so the
certificate lapsed a day to three early, and the compliance view counted a
valid one overdue.)
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta


def add_months(start: date, months: int) -> date:
    month0 = start.month - 1 + months
    year, month = start.year + month0 // 12, month0 % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def expires_on(completed: date, validity_months: int | None) -> date | None:
    """The day before the same date `validity_months` later; None if it never expires."""
    if not validity_months:
        return None
    same_day = add_months(completed, validity_months)
    if same_day.day < completed.day:
        # No such day that month: it would roll to the 1st of the next, and the
        # day before that is this one — the month's last.
        return same_day
    return same_day - timedelta(days=1)
