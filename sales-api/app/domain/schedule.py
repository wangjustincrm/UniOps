"""When a connection's job is due (user decision 2026-11-02): every N minutes,
unless paused, inside its optional active window — in Toronto time, so the
window follows daylight saving.

The window: `active_from`–`active_to` on `active_days` (empty = every day).
An end before the start crosses midnight, and belongs to the day it started:
Fri 22:00–02:00 covers Saturday 01:00 (and not Sunday 01:00 when Saturday is
not listed)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo("America/Toronto")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_days(raw: str | None) -> tuple[str, ...]:
    """'Mon,Wed' → ('Mon', 'Wed') in week order; empty → () (every day). Raises
    ValueError on an unknown day."""
    days = {d.strip().capitalize()[:3] for d in (raw or "").split(",") if d.strip()}
    bad = days - set(DAYS)
    if bad:
        raise ValueError(f"not a day: {', '.join(sorted(bad))} (Mon … Sun)")
    return tuple(d for d in DAYS if d in days)


def format_days(days) -> str | None:
    return ",".join(d for d in DAYS if d in set(days)) or None


@dataclass(frozen=True)
class Window:
    start: time | None = None
    end: time | None = None
    days: tuple[str, ...] = ()

    def contains(self, at: datetime) -> bool:
        local = at.astimezone(TORONTO)
        day, t = local.date(), local.time()
        if self.start is not None and self.end is not None and self.start != self.end:
            if self.start < self.end:
                if not self.start <= t < self.end:
                    return False
            elif t >= self.start:
                pass  # the evening part: today's window
            elif t < self.end:
                day -= timedelta(days=1)  # the early-morning part belongs to yesterday's window
            else:
                return False
        return not self.days or DAYS[day.weekday()] in self.days


def is_due(now: datetime, *, next_run_at: datetime | None, paused: bool, window: Window) -> bool:
    """Not paused, inside the window, and its next run time reached (none yet: now)."""
    return not paused and window.contains(now) and (next_run_at is None or now >= next_run_at)


def next_run(started_at: datetime, minutes: int) -> datetime:
    """The cadence runs from the start of the last run."""
    return started_at + timedelta(minutes=minutes)


def describe(minutes: int, window: Window, paused: bool = False) -> str:
    """"every 5 min, Mon–Fri 06:00–22:00" (the list's Schedule column says the same)."""
    out = f"every {minutes} min"
    if window.days and len(window.days) < 7:
        idx = [DAYS.index(d) for d in window.days]
        run = idx == list(range(idx[0], idx[-1] + 1)) and len(idx) > 2
        out += ", " + (f"{window.days[0]}–{window.days[-1]}" if run else " ".join(window.days))
    if window.start is not None and window.end is not None:
        out += f" {window.start:%H:%M}–{window.end:%H:%M}"
    return out + (" (paused)" if paused else "")
