"""Dates written into text by code (assistant answers, compare views) in the
on-screen format, UI-DATE-002: 2026-NOV-11 and 2026-NOV-11 14:30.

Accepts what those callers hold: ISO strings (as controlled_query serialises
them, with the plant offset), dates and datetimes. An aware value is shown on
the plant clock (UI-DATE-003); a naive date-time is a wall-clock reading and
is printed as written — the same rule as the web apps' shared formatter.
"""
from __future__ import annotations

from datetime import date, datetime

from app.core.plant_time import fmt_date, to_plant


def _parse(value) -> date | datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (date, datetime)):
        return value
    s = str(value).strip()
    try:
        if len(s) == 10:
            return date.fromisoformat(s)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def text_date(value, empty: str = "") -> str:
    """'2026-NOV-11' (an aware moment is taken on the plant clock)."""
    v = _parse(value)
    if v is None:
        return empty
    if isinstance(v, datetime) and v.tzinfo is not None:
        v = to_plant(v)
    return fmt_date(v.date() if isinstance(v, datetime) else v)


def text_datetime(value, empty: str = "") -> str:
    """'2026-NOV-11 14:30' for a moment; a bare date stays '2026-NOV-11'."""
    v = _parse(value)
    if v is None:
        return empty
    if not isinstance(v, datetime):
        return fmt_date(v)
    if v.tzinfo is not None:
        v = to_plant(v)
    return f"{fmt_date(v.date())} {v.hour:02d}:{v.minute:02d}"
