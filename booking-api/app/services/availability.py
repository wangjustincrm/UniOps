"""Pure availability functions — no DB, no I/O.

These are imported by:
  - app/api/v1/rooms.py  (employee listing + detail)
  - app/services/recommend.py  (Task 6 — conflict precheck)
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models.booking import Booking
from app.models.room import MeetingRoom

# How far ahead (minutes) counts as "starting_soon"
_STARTING_SOON_MINUTES = 15


def compute_room_status(
    room: MeetingRoom,
    bookings_today: list[Booking],
    now: datetime,
) -> str:
    """Return the real-time operational status of a meeting room.

    Status precedence (PRD 9.3.3):
        disabled | maintenance  →  from room.status (highest priority)
        in_use                 →  now is inside a confirmed booking
        starting_soon          →  next confirmed booking starts ≤15 min from now
        booked                 →  any confirmed booking remains today (> 15 min away)
        free                   →  nothing above applies

    Args:
        room:           The MeetingRoom ORM object.
        bookings_today: All bookings for this room today (any status).
        now:            Current tz-aware datetime (used for comparisons).

    Returns:
        One of: "free", "in_use", "starting_soon", "booked", "disabled", "maintenance".
    """
    # ── Highest priority: room hardware status ────────────────────────────────
    if room.status == "disabled":
        return "disabled"
    if room.status == "maintenance":
        return "maintenance"

    # Only confirmed bookings matter for occupancy checks
    confirmed = [b for b in bookings_today if b.status == "confirmed"]

    # ── in_use: now is inside [starts_at, ends_at) ───────────────────────────
    for b in confirmed:
        if b.starts_at <= now < b.ends_at:
            return "in_use"

    # ── starting_soon / booked: look at future bookings ─────────────────────-
    future = [b for b in confirmed if b.starts_at > now]
    if future:
        next_booking = min(future, key=lambda b: b.starts_at)
        minutes_until = (next_booking.starts_at - now).total_seconds() / 60
        if minutes_until <= _STARTING_SOON_MINUTES:
            return "starting_soon"
        return "booked"

    # ── free: nothing pending ─────────────────────────────────────────────────
    return "free"


def free_slots(
    room_open: tuple[time, time],
    busy: list[tuple[datetime, datetime]],
    day: date,
    slot_minutes: int,
    tz: ZoneInfo,
) -> list[tuple[datetime, datetime]]:
    """Return contiguous free gaps within the room's open window.

    Algorithm:
      1. Convert open hours to tz-aware datetimes for ``day``.
      2. Clamp each busy interval to [open_start, open_end].
      3. Sort + merge overlapping busy intervals.
      4. Walk the merged list, yielding gaps between consecutive busy blocks
         that are ≥ slot_minutes long.

    Args:
        room_open:    (open_time_start, open_time_end) as ``datetime.time`` values.
        busy:         List of (starts_at, ends_at) tuples — may extend outside
                      open hours; they will be clamped.
        day:          The calendar date being queried.
        slot_minutes: Minimum gap duration to include (e.g. 15).
        tz:           ZoneInfo for the room / system display timezone.

    Returns:
        Sorted list of (gap_start, gap_end) tz-aware datetimes representing
        contiguous free windows ≥ slot_minutes.
    """
    open_start = datetime(day.year, day.month, day.day,
                          room_open[0].hour, room_open[0].minute, tzinfo=tz)
    open_end = datetime(day.year, day.month, day.day,
                        room_open[1].hour, room_open[1].minute, tzinfo=tz)

    min_duration = timedelta(minutes=slot_minutes)

    if open_start >= open_end:
        return []

    # ── Clamp and filter busy intervals ──────────────────────────────────────
    clamped: list[tuple[datetime, datetime]] = []
    for b_start, b_end in busy:
        cs = max(b_start, open_start)
        ce = min(b_end, open_end)
        if cs < ce:
            clamped.append((cs, ce))

    if not clamped:
        # No busy time inside open hours — return the entire window if big enough
        if open_end - open_start >= min_duration:
            return [(open_start, open_end)]
        return []

    # ── Sort and merge overlapping clamped busy intervals ────────────────────
    clamped.sort(key=lambda t: t[0])
    merged: list[tuple[datetime, datetime]] = []
    cur_s, cur_e = clamped[0]
    for s, e in clamped[1:]:
        if s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            merged.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    merged.append((cur_s, cur_e))

    # ── Walk merged busy list and collect gaps ─────────────────────────────-
    gaps: list[tuple[datetime, datetime]] = []
    cursor = open_start

    for busy_start, busy_end in merged:
        if busy_start > cursor:
            gap_end = busy_start
            if gap_end - cursor >= min_duration:
                gaps.append((cursor, gap_end))
        cursor = max(cursor, busy_end)

    # Final gap after last busy block
    if open_end > cursor and open_end - cursor >= min_duration:
        gaps.append((cursor, open_end))

    return gaps
