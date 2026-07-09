"""Conflict detection and alternative room/time recommendation engine.

Produces:
  - find_conflicts(db, room_id, starts_at, ends_at, exclude_booking_ids) -> list[Booking]
  - suggest(db, *, room, starts_at, ends_at, attendee_count, equipment, cfg_rules) -> SuggestOut

Consumed by:
  - app/api/v1/precheck.py  (Task 6)
  - Task 7 (series expansion wiring)
  - Tasks 8, 13, 14
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.booking import Booking
from app.models.room import MeetingRoom
from app.schemas.room import RoomOut, RoomWithStatusOut
from app.services.availability import compute_room_status, free_slots


# ─────────────────────────────────────────────────────────────────────────────
# find_conflicts
# ─────────────────────────────────────────────────────────────────────────────

async def find_conflicts(
    db: AsyncSession,
    room_id,
    starts_at: datetime,
    ends_at: datetime,
    exclude_booking_ids: set | None = None,
) -> list[Booking]:
    """Return confirmed bookings that overlap [starts_at, ends_at).

    Overlap predicate (touching edges NOT a conflict):
        booking.starts_at < ends_at AND booking.ends_at > starts_at

    Args:
        db:                  AsyncSession.
        room_id:             UUID of the target room.
        starts_at:           Window start (inclusive).
        ends_at:             Window end (exclusive).
        exclude_booking_ids: Booking IDs to exclude (e.g. the booking being edited).

    Returns:
        List of conflicting Booking ORM objects, ordered by starts_at.
    """
    stmt = (
        select(Booking)
        .where(
            and_(
                Booking.room_id == room_id,
                Booking.status == "confirmed",
                Booking.starts_at < ends_at,
                Booking.ends_at > starts_at,
            )
        )
        .order_by(Booking.starts_at)
    )
    if exclude_booking_ids:
        stmt = stmt.where(Booking.id.not_in(list(exclude_booking_ids)))

    result = await db.execute(stmt)
    return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────────────────
# suggest helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_floor_int(floor: str | None) -> int | None:
    """Parse leading integer from floor string; non-parsable → None."""
    if floor is None:
        return None
    m = re.match(r"^(-?\d+)", str(floor).strip())
    if m:
        return int(m.group(1))
    return None


def _rank_candidate(
    candidate: MeetingRoom,
    target: MeetingRoom,
    attendee_count: int | None,
    equipment: list[str],
) -> tuple:
    """Return a sort key tuple (rank, sub_key...) for a candidate room.

    Ranking tiers (PRD 9.4.3):
      rank 0 — same floor AND capacity in band [needed, 2*needed]
               (when attendee_count given; without it, same floor only)
      rank 1 — same area OR adjacent floor (|floor_int_diff| == 1)
      rank 2 — everything else; sub-sorted by:
               (capacity_delta, abs(capacity - needed), missing_equipment_count)
    """
    target_floor_int = _parse_floor_int(target.floor)
    cand_floor_int = _parse_floor_int(candidate.floor)

    same_floor = (candidate.floor is not None and candidate.floor == target.floor)
    same_area = (candidate.area is not None and candidate.area == target.area)

    # Adjacent floor: both floors must be parsable ints, distance == 1
    adjacent_floor = False
    if target_floor_int is not None and cand_floor_int is not None:
        adjacent_floor = abs(target_floor_int - cand_floor_int) == 1

    needed = attendee_count or 0
    cap = candidate.capacity

    # capacity in band [needed, 2*needed]
    in_cap_band = (needed > 0 and needed <= cap <= 2 * needed)

    # Rank 0: same floor AND (capacity in band OR no attendee_count given)
    if same_floor:
        if attendee_count is None or in_cap_band:
            return (0, 0, 0, 0)

    # Rank 1: same area OR adjacent floor
    if same_area or adjacent_floor:
        return (1, 0, 0, 0)

    # Rank 2: everyone else; sub-sort by capacity_delta, abs(cap - needed), missing_equipment
    capacity_delta = max(0, needed - cap) if needed > 0 else 0
    cap_abs_diff = abs(cap - needed) if needed > 0 else 0
    cand_equip = set(candidate.equipment or [])
    missing_equipment_count = len([e for e in equipment if e not in cand_equip])

    return (2, capacity_delta, cap_abs_diff, missing_equipment_count)


# ─────────────────────────────────────────────────────────────────────────────
# suggest
# ─────────────────────────────────────────────────────────────────────────────

async def suggest(
    db: AsyncSession,
    *,
    room: MeetingRoom,
    starts_at: datetime,
    ends_at: datetime,
    attendee_count: int | None,
    equipment: list[str],
    cfg_rules: dict,
) -> dict:
    """Generate alternative room + time slot suggestions.

    Returns:
        {
            "nearest_slots": [{"starts_at": dt, "ends_at": dt}, ...],  # ≤3
            "alternative_rooms": [RoomWithStatusOut, ...],               # ≤5
        }
    """
    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)
    duration = ends_at - starts_at
    slot_minutes: int = cfg_rules.get("slot_minutes", 15)
    default_open_start_str: str = cfg_rules.get("default_open_start", "08:00")
    default_open_end_str: str = cfg_rules.get("default_open_end", "20:00")

    def _parse_time(s: str) -> time:
        h, m = s.split(":")
        return time(int(h), int(m))

    # ── nearest_slots: free gaps in target room on the requested day ──────────
    local_starts = starts_at.astimezone(tz)
    day = local_starts.date()

    open_start = room.open_time_start or _parse_time(default_open_start_str)
    open_end = room.open_time_end or _parse_time(default_open_end_str)

    # Load all confirmed bookings for the target room on that day
    day_start_utc = datetime(day.year, day.month, day.day, 0, 0, tzinfo=tz).astimezone(
        ZoneInfo("UTC")
    )
    day_end_utc = day_start_utc + timedelta(days=1)

    busy_result = await db.execute(
        select(Booking.starts_at, Booking.ends_at)
        .where(
            and_(
                Booking.room_id == room.id,
                Booking.status == "confirmed",
                Booking.starts_at < day_end_utc,
                Booking.ends_at > day_start_utc,
            )
        )
    )
    busy: list[tuple[datetime, datetime]] = [
        (row.starts_at, row.ends_at) for row in busy_result
    ]

    gaps = free_slots((open_start, open_end), busy, day, slot_minutes, tz)

    # Trim each gap to `duration`, anchored as close to starts_at as possible.
    #
    # For a gap [gap_start, gap_end) with requested start rs and duration d:
    #   offered_start = clamp(rs, gap_start, gap_end - d)
    #
    # This places the offered slot at rs when the gap fully contains rs+d,
    # at gap_start when rs falls before the gap, and at gap_end-d when
    # rs+d would overshoot the gap end.  The sort key uses the anchored
    # trimmed_start (not gap_start) so a large gap is offered near rs, not
    # at the gap's open edge.
    #
    # Grid alignment: snap trimmed_start DOWN to the nearest slot_minutes
    # boundary (floor toward rs) while remaining >= gap_start.
    slot_minutes_td = timedelta(minutes=slot_minutes)
    slot_candidates: list[tuple[datetime, datetime]] = []
    for gap_start, gap_end in gaps:
        gap_duration = gap_end - gap_start
        if gap_duration < duration:
            continue  # gap too short

        # Ideal anchor: clamp starts_at into the feasible range [gap_start, gap_end - duration]
        feasible_end = gap_end - duration
        raw_start = min(max(gap_start, starts_at), feasible_end)

        # Snap DOWN to the nearest slot_minutes grid tick (keeps us inside the gap)
        if slot_minutes > 0:
            epoch = gap_start  # grid origin = gap_start
            ticks_from_epoch = int((raw_start - epoch).total_seconds() // slot_minutes_td.total_seconds())
            snapped_start = epoch + ticks_from_epoch * slot_minutes_td
            # Ensure we didn't snap below gap_start (shouldn't happen, but guard it)
            snapped_start = max(snapped_start, gap_start)
            # Re-clamp so snapped_start + duration <= gap_end
            snapped_start = min(snapped_start, feasible_end)
        else:
            snapped_start = raw_start

        trimmed_start = snapped_start
        trimmed_end = trimmed_start + duration
        slot_candidates.append((trimmed_start, trimmed_end))

    # Sort by closeness of the *anchored* start to the requested starts_at
    slot_candidates.sort(key=lambda s: abs((s[0] - starts_at).total_seconds()))
    nearest = slot_candidates[:3]
    nearest_slots = [{"starts_at": s, "ends_at": e} for s, e in nearest]

    # ── alternative_rooms: available rooms free in the window ─────────────────
    candidates_result = await db.execute(
        select(MeetingRoom).where(
            and_(
                MeetingRoom.status == "available",
                MeetingRoom.id != room.id,
            )
        )
    )
    all_candidates: list[MeetingRoom] = list(candidates_result.scalars().all())

    # Filter to those free in the requested window
    if all_candidates:
        cand_ids = [c.id for c in all_candidates]
        busy_cand_result = await db.execute(
            select(Booking.room_id).where(
                and_(
                    Booking.room_id.in_(cand_ids),
                    Booking.status == "confirmed",
                    Booking.starts_at < ends_at,
                    Booking.ends_at > starts_at,
                )
            )
        )
        busy_cand_ids = {row.room_id for row in busy_cand_result}
        free_candidates = [c for c in all_candidates if c.id not in busy_cand_ids]
    else:
        free_candidates = []

    # Rank candidates
    free_candidates.sort(key=lambda c: _rank_candidate(c, room, attendee_count, equipment))

    # Cap at 5 and convert to RoomWithStatusOut
    top5 = free_candidates[:5]
    now = datetime.now(tz)

    # Fetch today's confirmed bookings for the top-5 candidates so we can
    # compute a real status_now (in_use / starting_soon / booked / free).
    # One grouped query covers all candidates.
    today_bookings_by_room: dict = {c.id: [] for c in top5}
    if top5:
        today_start_utc = datetime(now.year, now.month, now.day, 0, 0, tzinfo=tz).astimezone(
            ZoneInfo("UTC")
        )
        today_end_utc = today_start_utc + timedelta(days=1)
        top5_ids = [c.id for c in top5]
        today_result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.room_id.in_(top5_ids),
                    Booking.status == "confirmed",
                    Booking.starts_at < today_end_utc,
                    Booking.ends_at > today_start_utc,
                )
            )
        )
        for b in today_result.scalars().all():
            if b.room_id in today_bookings_by_room:
                today_bookings_by_room[b.room_id].append(b)

    alternative_rooms: list[RoomWithStatusOut] = []
    for cand in top5:
        room_out = RoomOut.model_validate(cand)
        bookings_today = today_bookings_by_room.get(cand.id, [])
        status_now = compute_room_status(cand, bookings_today, now)
        # next_meeting_at: derive from today's future confirmed bookings if available.
        # B-M-3: surfaced in room cards; compute from the same data we already have.
        future_today = [
            b for b in bookings_today
            if b.status == "confirmed" and b.starts_at > now
        ]
        next_meeting_at = min((b.starts_at for b in future_today), default=None)
        alt = RoomWithStatusOut(
            **room_out.model_dump(),
            status_now=status_now,
            next_meeting_at=next_meeting_at,
        )
        alternative_rooms.append(alt)

    return {
        "nearest_slots": nearest_slots,
        "alternative_rooms": alternative_rooms,
    }
