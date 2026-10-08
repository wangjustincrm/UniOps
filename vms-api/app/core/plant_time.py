"""The plant's local time (settings.REPORT_TIMEZONE, America/Toronto).

Times are stored in UTC. Anything a person reads as a day or a clock time —
"today" on the dashboard, times in emails, the after-hours window — must be
taken in the plant's zone. Using the UTC date made "today" roll over at
20:00 EDT / 19:00 EST (VMS PRD V2.7 §12 D-07 / D-18).
"""
from datetime import date, datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

from app.core.config import settings


def plant_tz() -> tzinfo:
    try:
        return ZoneInfo(settings.REPORT_TIMEZONE)
    except Exception:  # noqa: BLE001 — a bad tz setting degrades to UTC
        return timezone.utc


def plant_today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(plant_tz()).date()


def fmt_local(value) -> str:
    """'2026-10-01 14:30 EDT' for a datetime, ISO for a date, '—' for None."""
    if value is None:
        return "—"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(plant_tz()).strftime("%Y-%m-%d %H:%M %Z")
    return value.isoformat()
