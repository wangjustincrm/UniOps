"""The plant's time zone — the company setting, not the server's clock.

SIBLING COPY: every *-api/app/core/plant_time.py is this same file. Keep them
identical; change one, change all.

Times are stored in UTC (timestamptz). Anything a person reads as a day or a
clock time — "today", "this month", an accounting period, the date in a
document number, a time printed on a PDF or in an email, the hour a daily job
runs — must be taken in the plant's zone. The containers set no TZ, so
`date.today()`, `datetime.utcnow()` and SQL `CURRENT_DATE` / `now()` are all
UTC; in Toronto those roll over to tomorrow at 19:00 EST / 20:00 EDT.

The zone is `company_config.timezone` (Portal → Admin → Company Settings), an
IANA name such as "America/Toronto" — a name, not a fixed offset, so daylight
saving is handled. Every service reads the same row (they share one database):
once at startup and then every REFRESH_SECONDS from a background loop, so the
functions below are synchronous and cheap. Until the first read succeeds (or
if the column is missing / the value is not a valid zone) the default is used.

Display format (UI-DATE-002, same as the web apps): dates 2026-NOV-11,
date-times 2026-NOV-11 14:30.

NOT for external-system contracts: the zone NC expects or sends, WMS wall-clock
columns and sync cursors are fixed by those systems and must not follow this
setting.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from sqlalchemy import text

log = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "America/Toronto"
REFRESH_SECONDS = 60

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

_tz_name: str = DEFAULT_TIMEZONE
_tz: tzinfo = ZoneInfo(DEFAULT_TIMEZONE)


# ── The setting ─────────────────────────────────────────────────────────────

def is_valid_timezone(name: str | None) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
        return True
    except Exception:  # noqa: BLE001 — ZoneInfoNotFoundError, ValueError, …
        return False


def set_plant_tz(name: str | None) -> bool:
    """Adopt `name` if it is a valid IANA zone. Returns whether it was taken."""
    global _tz_name, _tz
    if not is_valid_timezone(name):
        return False
    _tz_name, _tz = name, ZoneInfo(name)  # type: ignore[arg-type]
    return True


def plant_tz_name() -> str:
    """IANA name, e.g. for SQL: func.timezone(plant_tz_name(), column)."""
    return _tz_name


def plant_tz() -> tzinfo:
    return _tz


async def refresh_plant_tz(engine) -> None:
    """Read company_config.timezone. Never raises: a failed read keeps the
    zone already in use (the column may not exist until epms-api migrates)."""
    try:
        async with engine.connect() as conn:
            name = (await conn.execute(
                text("SELECT timezone FROM company_config LIMIT 1")
            )).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        log.debug("plant timezone not read (%s); keeping %s", exc, _tz_name)
        return
    if name and name != _tz_name and not set_plant_tz(name):
        log.warning("company_config.timezone %r is not a valid zone; keeping %s", name, _tz_name)


async def plant_tz_refresh_loop(engine) -> None:
    """Background loop: re-read the setting every REFRESH_SECONDS."""
    while True:
        await asyncio.sleep(REFRESH_SECONDS)
        await refresh_plant_tz(engine)


# ── Clock ───────────────────────────────────────────────────────────────────

def plant_now() -> datetime:
    """Aware datetime in the plant's zone."""
    return datetime.now(timezone.utc).astimezone(_tz)


def to_plant(value: datetime) -> datetime:
    """An instant in the plant's zone. A naive datetime is taken as UTC (that
    is what the app stores)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_tz)


def plant_today(now: datetime | None = None) -> date:
    """Today's calendar date at the plant."""
    return to_plant(now).date() if now is not None else plant_now().date()


def plant_day_start(day: date) -> datetime:
    """Midnight at the plant on `day`, as an aware datetime (compare it with
    timestamptz columns directly)."""
    return datetime.combine(day, time.min, tzinfo=_tz)


def plant_day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) of `day` at the plant — DST days are 23 or 25 hours."""
    return plant_day_start(day), plant_day_start(day + timedelta(days=1))


def plant_month_start(day: date | None = None) -> datetime:
    """Midnight at the plant on the first of `day`'s month (default: now)."""
    d = day or plant_today()
    return plant_day_start(d.replace(day=1))


# ── Display (UI-DATE-002) ───────────────────────────────────────────────────

def fmt_date(value: date | datetime | None, empty: str = "—") -> str:
    """'2026-NOV-11'. A datetime is converted to the plant's zone first; a
    date is a calendar date and is printed as is."""
    if value is None:
        return empty
    if isinstance(value, datetime):
        value = to_plant(value)
    return f"{value.year:04d}-{_MONTHS[value.month - 1]}-{value.day:02d}"


def fmt_datetime(value: datetime | None, empty: str = "—", with_zone: bool = False) -> str:
    """'2026-NOV-11 14:30' in the plant's zone (with_zone: '… 14:30 EST')."""
    if value is None:
        return empty
    local = to_plant(value)
    out = f"{fmt_date(local)} {local.hour:02d}:{local.minute:02d}"
    return f"{out} {local.tzname()}" if with_zone else out
