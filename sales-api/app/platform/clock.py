"""The clock (≈ ABP's IClock / AbpClockOptions, user 2026-11-24): one place for "now" and for the business day.

- `clock().now()`        aware UTC, the wall time (Python). SQL's now() is the transaction's start: a different
                         instant, kept where it is used (see `business_date_sql`).
- `clock().today()`      the business date: now in the business zone (ClockOptions.business_timezone).
- `clock().to_business(t)`, `clock().zone`, `clock().at(day, time)`: the same zone for a moment or a day.
- `clock().normalize(t)` aware → UTC; naive → per ClockOptions.naive_is (UTC by default). Sites whose rule differs
                         (a naive value refused, a file's local time) keep their own rule.

The clock is a Strategy: SystemClock by default, FixedClock in tests — set with `use_clock(...)`, which stores it in
the options registry (no other global). Services that take a `clock=` callable default to `lambda: clock().now()`
(read at call time), so a configured clock applies to them too.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, func

from app.platform.options import ClockOptions, configure, options, replace


def business_timezone() -> str:
    """The business zone's name (e.g. "America/Toronto")."""
    return options(ClockOptions).business_timezone


def business_zone() -> ZoneInfo:
    return ZoneInfo(business_timezone())


class Clock:
    """The base: a subclass gives `now()`; the rest follows from it and the options."""

    def now(self) -> datetime:
        raise NotImplementedError

    @property
    def zone(self) -> ZoneInfo:
        return business_zone()

    def today(self) -> date:
        return self.now().astimezone(self.zone).date()

    def to_business(self, t: datetime) -> datetime:
        return t.astimezone(self.zone)

    def at(self, day: date, at: time) -> datetime:
        """That day's wall-clock time in the business zone."""
        return datetime.combine(day, at, tzinfo=self.zone)

    def normalize(self, t: datetime) -> datetime:
        if t.tzinfo is None:
            t = t.replace(tzinfo=self.zone if options(ClockOptions).naive_is == "business" else timezone.utc)
        return t.astimezone(timezone.utc)


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock(Clock):
    """A clock that stands still until moved (tests)."""

    def __init__(self, at: datetime) -> None:
        self.set(at)

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock needs an aware time")
        self._at = at.astimezone(timezone.utc)

    def advance(self, by: timedelta) -> None:
        self._at += by


_SYSTEM = SystemClock()


def clock() -> Clock:
    """The configured clock (ClockOptions.provider), else the system clock."""
    return options(ClockOptions).provider or _SYSTEM


def use_clock(c: Clock | None) -> None:
    """Set the clock (None: back to the system clock), keeping the other clock options."""
    configure(replace(options(ClockOptions), provider=c))


def utc_now() -> datetime:
    """`clock().now()` as a plain callable (a `clock=` default, a dataclass default_factory)."""
    return clock().now()


def business_time_sql(column: Any) -> Any:
    """A timestamptz column as wall time in the business zone (SQL)."""
    return func.timezone(business_timezone(), column)


def business_date_sql(column: Any = None) -> Any:
    """The business date of a timestamptz column — or, without one, of SQL's now(). The same zone as the Python clock;
    the instant is the database's (now() = the transaction's start), so a FixedClock does not freeze it."""
    return cast(business_time_sql(func.now() if column is None else column), Date)


__all__ = ["Clock", "FixedClock", "SystemClock", "business_date_sql", "business_time_sql", "business_timezone",
           "business_zone", "clock", "use_clock", "utc_now"]
