"""Turning a pair of calendar days into the instants that bound them.

A register filtered by date asks a question about days at this plant — "what
happened in August" — while the records carry instants. Midnight on 1 August
in Ontario is 04:00 UTC, so comparing a stored instant against a bare date
drags four hours of every day into its neighbour: an injury at 9pm on the 31st
would answer to September, and the month it is missing from is the month
somebody is reporting on.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ONTARIO = ZoneInfo("America/Toronto")


def day_bounds(
    from_day: date | None, to_day: date | None
) -> tuple[datetime | None, datetime | None]:
    """UTC instants for an inclusive range of local days.

    The upper bound is the midnight *after* the last day and is meant to be
    compared with `<`: that covers the whole final day without depending on
    how many microseconds a timestamp happens to carry.
    """
    start = (datetime.combine(from_day, time.min, ONTARIO).astimezone(timezone.utc)
             if from_day else None)
    end = (datetime.combine(to_day + timedelta(days=1), time.min, ONTARIO)
           .astimezone(timezone.utc) if to_day else None)
    return start, end
