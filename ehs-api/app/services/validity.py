"""When a training record expires, from the course it is for.

★ Round 9, question 5: "each course should carry its own expiry date. … if I
create a course with a 12 month expiry, have the certificate expire in 12
months minus a day." Calendar months, not thirty-day ones: the approximation
put a 36-month course's expiry 18 days early. A month that has no such day
(31 January plus one month) lands on its last day, then less a day.
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
    return add_months(completed, validity_months) - timedelta(days=1)
