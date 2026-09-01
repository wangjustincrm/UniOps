"""When the next inspection is due, and whose turn it is.

Pure functions, no database. Recurrence is where scheduling goes wrong in ways
nobody notices for a month — a monthly inspection due on the 31st, a rotation
that shifts because one occurrence was deleted — so it is kept testable and out
of the sweep.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

# semiannual is here for the equipment register: fall-arrest harnesses and
# eyewash stations are checked twice a year, and expressing that as "every two
# quarters" would put the schedule out of step with what the label says.
FREQUENCIES = ("daily", "weekly", "monthly", "quarterly", "semiannual", "annual")


def add_months(day: date, months: int) -> date:
    """Same day next month, clamped to the last day where it does not exist.

    A monthly inspection set up on 31 January must land on 28 February and then
    on 31 March — not drift to the 28th of every following month. Clamping the
    result while keeping the original day for the next calculation is what the
    caller gets by always advancing from the schedule's own anchor rather than
    from the clamped value.
    """
    total = (day.year * 12 + day.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def next_due(current: date, frequency: str) -> date:
    """The occurrence after `current`."""
    if frequency == "daily":
        return current + timedelta(days=1)
    if frequency == "weekly":
        return current + timedelta(weeks=1)
    if frequency == "monthly":
        return add_months(current, 1)
    if frequency == "quarterly":
        return add_months(current, 3)
    if frequency == "semiannual":
        return add_months(current, 6)
    if frequency == "annual":
        return add_months(current, 12)
    raise ValueError(f"unknown frequency {frequency!r}; known: {', '.join(FREQUENCIES)}")


def rotation_pick(items: list, index: int):
    """Whose turn it is. Returns None for an empty rotation.

    The index is allowed to run past the end and wraps, so a department added to
    the list mid-cycle does not require the cursor to be corrected.
    """
    if not items:
        return None
    return items[index % len(items)]


def due_occurrences(
    *, next_due_on: date, frequency: str, today: date, lead_days: int, limit: int = 24
) -> list[date]:
    """Every occurrence that should exist by now, oldest first.

    Normally one. More than one means the sweep has not run for a while — a
    weekend outage, a container that stayed down — and every missed occurrence
    must still be created, because a month with no recorded inspection and a
    month whose inspection was never scheduled look identical afterwards and
    only one of them is a finding.

    `limit` stops a schedule whose `next_due_on` was set years in the past from
    generating hundreds of runs in one tick.
    """
    if frequency not in FREQUENCIES:
        raise ValueError(f"unknown frequency {frequency!r}")

    horizon = today + timedelta(days=max(lead_days, 0))
    out: list[date] = []
    cursor = next_due_on
    while cursor <= horizon and len(out) < limit:
        out.append(cursor)
        cursor = next_due(cursor, frequency)
    return out
