"""POST /bookings, PATCH /bookings/{id}, POST /bookings/{id}/cancel, GET /bookings/mine.

Validation order (per task-7-brief):
  1. Payload sanity: ends_at > starts_at; 15-min grid; duration within [min, max].
  2. Room exists (404); room.status == "available" (422).
  3. Open-hours window (LOCAL time); starts_at within advance window.
     needs_video_conf equipment check.
  4. Series expansion (ValueError → 422); ALL occurrences prechecked via
     find_conflicts; ANY conflict → 400 (nothing persisted).
  5. INSERT all in one transaction; IntegrityError → 400 (concurrency backstop).
  6. Audit rows; enqueue stub; 201.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import SessionDep
from app.core.permissions import AdminUser, CurrentUser, is_booking_admin
from app.crud.booking import create_booking_records
from app.crud.config import get_or_create_config
from app.models.audit import BookingAuditLog
from app.models.booking import Booking
from app.models.room import MeetingRoom
from app.models.user_mirror import User
from app.schemas.booking import (
    BookingCreate, BookingCreatedOut, BookingOut, BookingSlimOut, BookingUpdate,
    DaySummaryOut, DaySummaryRoom, DaySummaryBookingOut, SeriesUpdate,
)
from app.services.notifications import enqueue
from app.services.recurrence import SeriesSpec, build_rrule_string, expand_series
from app.services.recommend import find_conflicts, suggest

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _validate_grid(starts_at: datetime, ends_at: datetime, tz: ZoneInfo) -> None:
    """Raise 422 if starts_at/ends_at are not on a 15-min boundary."""
    for label, dt in [("starts_at", starts_at), ("ends_at", ends_at)]:
        local_dt = dt.astimezone(tz)
        if local_dt.minute % 15 != 0 or local_dt.second != 0 or local_dt.microsecond != 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{label} must be aligned to a 15-minute boundary (minute % 15 == 0, no seconds/microseconds)",
            )


def _validate_duration(starts_at: datetime, ends_at: datetime, min_dur: int, max_dur: int) -> None:
    """Raise 422 if duration is outside [min_dur, max_dur] minutes."""
    if ends_at <= starts_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ends_at must be after starts_at",
        )
    duration_minutes = (ends_at - starts_at).total_seconds() / 60
    if duration_minutes < min_dur:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Booking duration must be at least {min_dur} minutes",
        )
    if duration_minutes > max_dur:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Booking duration must not exceed {max_dur} minutes",
        )


def _validate_open_hours(
    starts_at: datetime,
    ends_at: datetime,
    room: MeetingRoom,
    tz: ZoneInfo,
    default_open_start: str,
    default_open_end: str,
) -> None:
    """Raise 422 if booking is outside room open hours."""
    open_start = room.open_time_start or _parse_time(default_open_start)
    open_end = room.open_time_end or _parse_time(default_open_end)

    local_starts = starts_at.astimezone(tz)
    local_ends = ends_at.astimezone(tz)

    start_time = local_starts.time().replace(second=0, microsecond=0)
    end_time = local_ends.time().replace(second=0, microsecond=0)
    if end_time == time(0, 0):
        end_time = time(23, 59)

    if start_time < open_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Booking starts before room open time ({open_start})",
        )
    if end_time > open_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Booking ends after room close time ({open_end})",
        )


def _validate_advance_window(
    starts_at: datetime,
    room: MeetingRoom,
    tz: ZoneInfo,
    advance_days: int,
) -> None:
    """Raise 422 if starts_at is outside the advance booking window."""
    now = datetime.now(tz)
    room_advance_days = room.advance_booking_days if room.advance_booking_days is not None else advance_days
    advance_cutoff = now + timedelta(days=room_advance_days)
    if starts_at > advance_cutoff:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Booking starts more than {room_advance_days} days in the future",
        )
    if starts_at < now - timedelta(minutes=5):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Booking starts in the past",
        )


async def _build_conflict_response(
    db: AsyncSession,
    room: MeetingRoom,
    starts_at: datetime,
    ends_at: datetime,
    conflicts: list[Booking],
    cfg_rules: dict,
    needs_video_conf: bool = False,
) -> JSONResponse:
    """Build a flat 400 conflict JSON response with suggestions."""
    attendee_count = None
    suggest_result = await suggest(
        db,
        room=room,
        starts_at=starts_at,
        ends_at=ends_at,
        attendee_count=attendee_count,
        equipment=["video_conf"] if needs_video_conf else [],
        cfg_rules=cfg_rules,
    )

    seen_ids: set[uuid.UUID] = set()
    unique_conflicts: list[Booking] = []
    for c in conflicts:
        if c.id not in seen_ids:
            seen_ids.add(c.id)
            unique_conflicts.append(c)

    conflicts_slim = await _build_slim_out_list(db, unique_conflicts)

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "detail": "conflict",
            "conflicts": [c.model_dump(mode="json") for c in conflicts_slim],
            "occurrence_conflicts": [],
            "suggestions": {
                "nearest_slots": [
                    {
                        "starts_at": s["starts_at"].isoformat(),
                        "ends_at": s["ends_at"].isoformat(),
                    }
                    for s in suggest_result["nearest_slots"]
                ],
                "alternative_rooms": [
                    r.model_dump(mode="json") for r in suggest_result["alternative_rooms"]
                ],
            },
        },
    )


def _make_booking_out(
    booking: Booking,
    room: MeetingRoom,
    organizer_name: str,
) -> BookingOut:
    return BookingOut(
        id=booking.id,
        title=booking.title,
        description=booking.description,
        starts_at=booking.starts_at,
        ends_at=booking.ends_at,
        organizer_name=organizer_name,
        room_id=booking.room_id,
        room_name=room.name,
        room_code=room.code,
        status=booking.status,
        attendee_ids=[uuid.UUID(str(aid)) for aid in (booking.attendee_ids or [])],
        series_id=booking.series_id,
        rrule=booking.rrule,
        sync_status=booking.sync_status,
        ical_sequence=booking.ical_sequence,
    )


async def _resolve_organizer_name(db: AsyncSession, organizer_id: uuid.UUID) -> str:
    result = await db.execute(
        select(User.full_name).where(User.id == organizer_id)
    )
    name = result.scalar_one_or_none()
    return name or "Unknown"


async def _build_slim_out_list(
    db: AsyncSession,
    conflict_rows: list[Booking],
) -> list[BookingSlimOut]:
    organizer_ids = list({b.organizer_id for b in conflict_rows})
    organizer_names: dict[uuid.UUID, str] = {}
    if organizer_ids:
        user_result = await db.execute(
            select(User.id, User.full_name).where(User.id.in_(organizer_ids))
        )
        organizer_names = {row.id: row.full_name for row in user_result}
    return [
        BookingSlimOut(
            id=b.id,
            title=b.title,
            starts_at=b.starts_at,
            ends_at=b.ends_at,
            organizer_name=organizer_names.get(b.organizer_id, "Unknown"),
        )
        for b in conflict_rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# POST /bookings
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", response_model=BookingCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_booking(
    body: BookingCreate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Create a single booking or a recurring series.

    Auto-approves (status=confirmed) immediately — no approval workflow needed
    for room bookings. All occurrences share one transaction; any conflict or
    constraint violation causes a full rollback and 400 response.
    """
    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)
    organizer_id = uuid.UUID(current_user["sub"])

    # ── Step 1: Payload sanity ────────────────────────────────────────────────

    if body.ends_at <= body.starts_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ends_at must be after starts_at",
        )

    # 15-minute grid alignment (minute % 15 == 0, second == 0, microsecond == 0)
    for label, dt in [("starts_at", body.starts_at), ("ends_at", body.ends_at)]:
        local_dt = dt.astimezone(tz)
        if local_dt.minute % 15 != 0 or local_dt.second != 0 or local_dt.microsecond != 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{label} must be aligned to a 15-minute boundary (minute % 15 == 0, no seconds/microseconds)",
            )

    # Duration check
    config = await get_or_create_config(db)
    cfg_rules: dict = config.rules or {}
    min_dur = cfg_rules.get("min_duration_minutes", 15)
    max_dur = cfg_rules.get("max_duration_minutes", 240)
    advance_days = cfg_rules.get("advance_days", 30)
    default_open_start = cfg_rules.get("default_open_start", "08:00")
    default_open_end = cfg_rules.get("default_open_end", "20:00")

    duration_minutes = (body.ends_at - body.starts_at).total_seconds() / 60
    if duration_minutes < min_dur:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Booking duration must be at least {min_dur} minutes",
        )
    if duration_minutes > max_dur:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Booking duration must not exceed {max_dur} minutes",
        )

    # ── Step 2: Room exists and is bookable ──────────────────────────────────

    result = await db.execute(select(MeetingRoom).where(MeetingRoom.id == body.room_id))
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found",
        )
    if room.status != "available":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Room is not bookable (status is not 'available')",
        )

    # ── Step 3: Open-hours window, advance window, equipment ─────────────────

    open_start = room.open_time_start or _parse_time(default_open_start)
    open_end = room.open_time_end or _parse_time(default_open_end)

    local_starts = body.starts_at.astimezone(tz)
    local_ends = body.ends_at.astimezone(tz)

    # Both start and end must be within open_time_start..open_time_end
    start_time = local_starts.time().replace(second=0, microsecond=0)
    end_time = local_ends.time().replace(second=0, microsecond=0)
    # Handle midnight: if end_time == midnight it means end of day
    if end_time == time(0, 0):
        end_time = time(23, 59)

    if start_time < open_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Booking starts before room open time ({open_start})",
        )
    # Half-open interval: a booking ending exactly at open_end is ALLOWED
    # (e.g. open_end=20:00 accepts a booking ending 20:00 — it merely releases
    # the room at close time, not after it).
    if end_time > open_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Booking ends after room close time ({open_end})",
        )

    # Advance window: starts_at must be within now .. now + advance_days
    now = datetime.now(tz)
    room_advance_days = room.advance_booking_days if room.advance_booking_days is not None else advance_days
    advance_cutoff = now + timedelta(days=room_advance_days)
    if body.starts_at > advance_cutoff:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Booking starts more than {room_advance_days} days in the future",
        )
    if body.starts_at < now - timedelta(minutes=5):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Booking starts in the past",
        )

    # Video conference equipment check
    if body.needs_video_conf:
        room_equipment = list(room.equipment or [])
        if "video_conf" not in room_equipment:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Room does not have video conferencing equipment (video_conf)",
            )

    # ── Step 4: Series expansion + conflict check ─────────────────────────────

    series_truncated: bool = False
    if body.series is not None:
        spec: SeriesSpec = body.series
        try:
            occurrences, series_truncated = expand_series(
                body.starts_at,
                body.ends_at,
                spec,
                advance_days=room_advance_days,
                tz=tz,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )
        if not occurrences:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Series expansion produced no occurrences within the advance booking window",
            )
        series_id = uuid.uuid4()
        rrule = build_rrule_string(spec, actual_count=len(occurrences))
    else:
        occurrences = [(body.starts_at, body.ends_at)]
        series_id = None
        rrule = None

    # Precheck ALL occurrences for conflicts
    all_conflict_rows: list[Booking] = []
    first_conflicting_occ: tuple[datetime, datetime] | None = None
    occurrence_conflicts: list[dict] = []

    for occ_start, occ_end in occurrences:
        conflicts = await find_conflicts(db, body.room_id, occ_start, occ_end)
        if conflicts:
            all_conflict_rows.extend(conflicts)
            occ_local_date = occ_start.astimezone(tz).date().isoformat()
            occurrence_conflicts.append({
                "date": occ_local_date,
                "conflicts": await _build_slim_out_list(db, conflicts),
            })
            if first_conflicting_occ is None:
                first_conflicting_occ = (occ_start, occ_end)

    if all_conflict_rows:
        # Generate suggestions for the FIRST conflicting occurrence.
        # first_conflicting_occ is always set when all_conflict_rows is non-empty
        # (the loop sets it on the first conflict iteration), but use an explicit
        # guard instead of a bare assert so the invariant survives -O optimisation.
        if first_conflicting_occ is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal error: conflict detected but no occurrence tracked",
            )
        first_start, first_end = first_conflicting_occ
        suggest_result = await suggest(
            db,
            room=room,
            starts_at=first_start,
            ends_at=first_end,
            attendee_count=len(body.attendee_ids) if body.attendee_ids else None,
            equipment=["video_conf"] if body.needs_video_conf else [],
            cfg_rules=cfg_rules,
        )

        # Build deduplicated conflicts list (unique by id)
        seen_ids: set[uuid.UUID] = set()
        unique_conflicts: list[Booking] = []
        for c in all_conflict_rows:
            if c.id not in seen_ids:
                seen_ids.add(c.id)
                unique_conflicts.append(c)

        conflicts_slim = await _build_slim_out_list(db, unique_conflicts)

        # Return a flat 400 body: {"detail": "conflict", "conflicts": [...], ...}
        # Using JSONResponse directly so FastAPI does not nest it under {"detail": {...}}
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": "conflict",
                "conflicts": [c.model_dump(mode="json") for c in conflicts_slim],
                "occurrence_conflicts": [
                    {
                        "date": oc["date"],
                        "conflicts": [c.model_dump(mode="json") for c in oc["conflicts"]],
                    }
                    for oc in occurrence_conflicts
                ],
                "suggestions": {
                    "nearest_slots": [
                        {
                            "starts_at": s["starts_at"].isoformat(),
                            "ends_at": s["ends_at"].isoformat(),
                        }
                        for s in suggest_result["nearest_slots"]
                    ],
                    "alternative_rooms": [
                        r.model_dump(mode="json") for r in suggest_result["alternative_rooms"]
                    ],
                },
            },
        )

    # ── Step 5: INSERT all in one transaction ─────────────────────────────────

    calendar_uid = f"{uuid.uuid4()}@uniops"
    organizer_name = await _resolve_organizer_name(db, organizer_id)

    try:
        bookings = await create_booking_records(
            db,
            payload=body,
            occurrences=occurrences,
            organizer_id=organizer_id,
            series_id=series_id,
            rrule=rrule,
            calendar_uid=calendar_uid,
            room=room,
            organizer_name=organizer_name,
        )
    except IntegrityError:
        await db.rollback()
        # Concurrency backstop: another transaction committed between our precheck
        # and our INSERT.  Re-query ALL occurrences (not just occurrence 1) so the
        # 400 body accurately identifies which slot(s) were grabbed.  Build the
        # same {detail, conflicts, occurrence_conflicts, suggestions} shape as the
        # precheck path so callers need no special-case handling.
        ie_all_conflict_rows: list[Booking] = []
        ie_first_conflicting_occ: tuple[datetime, datetime] | None = None
        ie_occurrence_conflicts: list[dict] = []

        for occ_start, occ_end in occurrences:
            occ_conflicts = await find_conflicts(db, body.room_id, occ_start, occ_end)
            if occ_conflicts:
                ie_all_conflict_rows.extend(occ_conflicts)
                occ_local_date = occ_start.astimezone(tz).date().isoformat()
                ie_occurrence_conflicts.append({
                    "date": occ_local_date,
                    "conflicts": await _build_slim_out_list(db, occ_conflicts),
                })
                if ie_first_conflicting_occ is None:
                    ie_first_conflicting_occ = (occ_start, occ_end)

        if ie_all_conflict_rows:
            # Deduplicate and build slim list
            ie_seen_ids: set[uuid.UUID] = set()
            ie_unique: list[Booking] = []
            for c in ie_all_conflict_rows:
                if c.id not in ie_seen_ids:
                    ie_seen_ids.add(c.id)
                    ie_unique.append(c)
            conflicts_slim = await _build_slim_out_list(db, ie_unique)
        else:
            # Race resolved itself (the conflicting booking vanished between the
            # IntegrityError and our re-query).  Return a safe 400 with empty lists
            # so the client can retry cleanly.
            conflicts_slim = []

        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": (
                    "conflict"
                    if ie_all_conflict_rows
                    else "Booking conflict detected concurrently; please retry"
                ),
                "conflicts": [c.model_dump(mode="json") for c in conflicts_slim],
                "occurrence_conflicts": [
                    {
                        "date": oc["date"],
                        "conflicts": [c.model_dump(mode="json") for c in oc["conflicts"]],
                    }
                    for oc in ie_occurrence_conflicts
                ],
                "suggestions": None,
            },
        )

    # ── Step 6: Notifications stub + 201 ─────────────────────────────────────

    # INVARIANT: notification failure must NEVER fail or roll back the booking.
    # Wrap enqueue in try/except so that even when Task 10 replaces this stub
    # with real sending, a transient notification error is logged and swallowed —
    # the booking is already committed at this point.
    try:
        await enqueue(db, bookings, "created", rrule=rrule)
    except Exception:
        logger.exception(
            "enqueue failed for booking(s) %s — notification suppressed, booking committed",
            [str(b.id) for b in bookings],
        )

    bookings_out = [_make_booking_out(b, room, organizer_name) for b in bookings]

    return BookingCreatedOut(
        bookings=bookings_out,
        series_id=series_id,
        series_truncated=series_truncated,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /bookings/mine
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/mine", response_model=list[BookingOut])
async def get_my_bookings(
    db: SessionDep,
    current_user: CurrentUser,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Return bookings where the current user is the organizer.

    Ordered by starts_at descending. Room summary resolved via a single JOIN
    (no N+1 query).

    limit: max results to return (1–500, default 200).
    offset: number of results to skip (default 0).
    """
    organizer_id = uuid.UUID(current_user["sub"])

    # Fetch bookings with room in one query
    stmt = (
        select(Booking, MeetingRoom)
        .join(MeetingRoom, Booking.room_id == MeetingRoom.id)
        .where(Booking.organizer_id == organizer_id)
        .order_by(Booking.starts_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()

    organizer_name = await _resolve_organizer_name(db, organizer_id)

    return [
        _make_booking_out(booking, room, organizer_name)
        for booking, room in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# GET /bookings/day
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/day", response_model=DaySummaryOut)
async def get_day_summary(
    db: SessionDep,
    current_user: CurrentUser,
    date: str = Query(..., description="Calendar date in YYYY-MM-DD format (interpreted in DISPLAY_TIMEZONE)"),
):
    """Return all rooms and confirmed bookings for a single calendar day.

    `date` is a local calendar day in settings.DISPLAY_TIMEZONE.
    The query window is [date 00:00 local, next day 00:00 local) converted to UTC.
    Rooms with status 'disabled' are excluded; maintenance rooms are included
    (frontend renders them greyed out).
    Organizer names are resolved via a single bulk JOIN (no N+1).
    """
    from datetime import date as date_type

    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)

    # Parse date string
    try:
        parsed_date = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date must be in YYYY-MM-DD format",
        )

    # Local-day window → UTC
    # Construct next-day midnight from the DATE (not from timedelta) so DST
    # transitions are handled correctly: timedelta(days=1) adds 86 400 s which
    # gives the wrong answer on 23-/25-hour DST days.
    day_start_local = datetime(parsed_date.year, parsed_date.month, parsed_date.day, 0, 0, 0, tzinfo=tz)
    next_day = parsed_date + timedelta(days=1)
    day_end_local = datetime(next_day.year, next_day.month, next_day.day, 0, 0, 0, tzinfo=tz)
    day_start_utc = day_start_local.astimezone(timezone.utc)
    day_end_utc = day_end_local.astimezone(timezone.utc)

    # Config defaults for open hours
    config = await get_or_create_config(db)
    cfg_rules: dict = config.rules or {}
    open_start = cfg_rules.get("default_open_start", "08:00")
    open_end = cfg_rules.get("default_open_end", "20:00")

    # All non-disabled rooms, sorted by floor then name
    rooms_result = await db.execute(
        select(MeetingRoom)
        .where(MeetingRoom.status != "disabled")
        .order_by(MeetingRoom.floor.nulls_last(), MeetingRoom.name)
    )
    rooms = list(rooms_result.scalars().all())

    # Confirmed bookings overlapping the day window, scoped to non-disabled rooms
    # only (prevents bookings on disabled rooms from leaking into the response
    # and corrupting the empty-state check on the frontend).
    room_ids = [r.id for r in rooms]
    bookings_result = await db.execute(
        select(Booking, User.full_name)
        .join(User, Booking.organizer_id == User.id, isouter=True)
        .where(
            Booking.status == "confirmed",
            Booking.starts_at < day_end_utc,
            Booking.ends_at > day_start_utc,
            Booking.room_id.in_(room_ids),
        )
    )
    booking_rows = bookings_result.all()

    return DaySummaryOut(
        date=parsed_date.isoformat(),
        open_start=open_start,
        open_end=open_end,
        timezone=settings.DISPLAY_TIMEZONE,
        rooms=[
            DaySummaryRoom(
                id=r.id,
                name=r.name,
                code=r.code,
                floor=r.floor,
                area=r.area,
                capacity=r.capacity,
                status=r.status,
            )
            for r in rooms
        ],
        bookings=[
            DaySummaryBookingOut(
                id=b.id,
                title=b.title,
                starts_at=b.starts_at,
                ends_at=b.ends_at,
                organizer_name=organizer_name or "Unknown",
                room_id=b.room_id,
            )
            for b, organizer_name in booking_rows
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /bookings/series/{series_id}
# NOTE: This MUST be registered BEFORE PATCH /{booking_id} so FastAPI matches
# the literal path segment "series" before the wildcard {booking_id}.
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/series/{series_id}")
async def update_booking_series(
    series_id: uuid.UUID,
    body: SeriesUpdate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Edit all FUTURE confirmed occurrences of a recurring series.

    Authorization: organizer (any member of the series) OR admin
    (manage_meeting_rooms via the Access Control Matrix).
    Unauthorized callers — including unknown series_id — get 404 (anti-probing).

    Semantics:
      - target rows = series' confirmed occurrences with starts_at > now
      - None left → 400 "series_fully_started"
      - room_id given: room must exist + status=available (else 404/422)
      - start_time/end_time given (HH:MM local): 15-min grid, duration within
        config min/max, inside room open hours; applied to each occurrence's
        own calendar date.
      - Conflict check: each future occurrence's new window against OTHER
        bookings (series own IDs excluded). ANY conflict → 400 flat body with
        occurrence_conflicts; nothing persisted.
      - Apply in one transaction; ical_sequence = max(series)+1 for ALL future
        rows; sync_status = "pending"; one audit row per updated booking.
      - ONE enqueue() for the first future occurrence (same UID + bumped SEQUENCE
        + RRULE) so Outlook updates the entire series.

    NOTE: Outlook rewrites past occurrences too when a series UPDATE is received;
    the DB intentionally keeps historical rows unchanged — accepted tradeoff.
    """
    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)
    actor_id = uuid.UUID(current_user["sub"])
    is_admin = await is_booking_admin(current_user, db)

    # ── Fetch ALL occurrences of the series, any status ───────────────────────
    # Individually-cancelled occurrences are fetched for two reasons:
    #   1. their times must follow series edits, so EXDATE keeps matching the
    #      instants Outlook computes from the RRULE;
    #   2. they are where the EXDATE list comes from (see notifications.py).
    # They must NOT take part in authorization, 404 / room resolution, conflict
    # checks or the returned count — confirmed_series_bookings owns all of that,
    # unchanged.
    series_result = await db.execute(
        select(Booking)
        .where(Booking.series_id == series_id)
        .order_by(Booking.starts_at)
    )
    all_series_bookings = list(series_result.scalars().all())
    confirmed_series_bookings = [
        b for b in all_series_bookings if b.status == "confirmed"
    ]

    if not confirmed_series_bookings:
        # No confirmed bookings with this series_id → treat as not found.
        # Keyed on confirmed rows, not all rows: a series whose occurrences were
        # every one cancelled must still read as "not found", exactly as before.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")

    # Authorization: must be organizer of the series or admin
    # (anti-probing: return 404 not 403)
    organizer_id = confirmed_series_bookings[0].organizer_id
    if actor_id != organizer_id and not is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series not found")

    # ── Target rows ───────────────────────────────────────────────────────────
    # future_bookings keeps its old meaning (future + confirmed) and its old
    # responsibilities: series_fully_started, room resolution, conflict checks,
    # the invite anchor and the returned count.
    now = datetime.now(tz)
    future_bookings = [b for b in confirmed_series_bookings if b.starts_at > now]
    # Cancelled future occurrences only follow the new times and feed EXDATE.
    future_cancelled = [
        b for b in all_series_bookings
        if b.status == "cancelled" and b.starts_at > now
    ]

    if not future_bookings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="series_fully_started",
        )

    # ── Room resolution ───────────────────────────────────────────────────────
    current_room_id = future_bookings[0].room_id
    room_explicitly_changed = body.room_id is not None and body.room_id != current_room_id

    if room_explicitly_changed:
        new_room_result = await db.execute(
            select(MeetingRoom).where(MeetingRoom.id == body.room_id)
        )
        effective_room = new_room_result.scalar_one_or_none()
        if effective_room is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
        effective_room_id = body.room_id
    else:
        effective_room_id = current_room_id
        # Always load the current room — needed for status check (C3) and open-hours validation
        cur_room_result = await db.execute(
            select(MeetingRoom).where(MeetingRoom.id == current_room_id)
        )
        effective_room = cur_room_result.scalar_one_or_none()

    # Always validate room status (C3: maintenance room must block even title-only edits)
    if effective_room is None or effective_room.status != "available":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Room is not bookable (status is not 'available')",
        )

    # ── Time validation (if times provided) ──────────────────────────────────
    config = await get_or_create_config(db)
    cfg_rules: dict = config.rules or {}
    min_dur = cfg_rules.get("min_duration_minutes", 15)
    max_dur = cfg_rules.get("max_duration_minutes", 240)
    default_open_start = cfg_rules.get("default_open_start", "08:00")
    default_open_end = cfg_rules.get("default_open_end", "20:00")

    new_start_h: int = 0
    new_start_m: int = 0
    new_end_h: int = 0
    new_end_m: int = 0

    if body.start_time is not None:
        # Parse the new times — schema already validated HH:MM format
        try:
            new_start_h, new_start_m = [int(x) for x in body.start_time.split(":")]
            new_end_h, new_end_m = [int(x) for x in body.end_time.split(":")]  # type: ignore[union-attr]
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="start_time and end_time must be in HH:MM format",
            )

        # 15-min grid check
        if new_start_m % 15 != 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="start_time must be aligned to a 15-minute boundary",
            )
        if new_end_m % 15 != 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="end_time must be aligned to a 15-minute boundary",
            )

        # Check duration using the first future occurrence's date as a proxy
        sample_date = future_bookings[0].starts_at.astimezone(tz).date()
        sample_start = datetime(sample_date.year, sample_date.month, sample_date.day,
                                new_start_h, new_start_m, tzinfo=tz)
        sample_end = datetime(sample_date.year, sample_date.month, sample_date.day,
                              new_end_h, new_end_m, tzinfo=tz)

        if sample_end <= sample_start:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="end_time must be after start_time",
            )

        duration_minutes = (sample_end - sample_start).total_seconds() / 60
        if duration_minutes < min_dur:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Booking duration must be at least {min_dur} minutes",
            )
        if duration_minutes > max_dur:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Booking duration must not exceed {max_dur} minutes",
            )

        # Open-hours check against the effective room (always loaded above)
        _validate_open_hours(sample_start, sample_end, effective_room, tz,
                             default_open_start, default_open_end)

    # ── Compute new windows for each future occurrence ────────────────────────
    series_ids_set = {b.id for b in all_series_bookings}

    def _new_window(b: Booking) -> tuple[Booking, datetime, datetime]:
        """Return (booking, new_starts, new_ends) for one occurrence.

        The occurrence keeps its own date and takes the series' new time of day.
        """
        if body.start_time is None:
            return (b, b.starts_at, b.ends_at)
        local_date = b.starts_at.astimezone(tz).date()
        return (
            b,
            datetime(local_date.year, local_date.month, local_date.day,
                     new_start_h, new_start_m, tzinfo=tz),
            datetime(local_date.year, local_date.month, local_date.day,
                     new_end_h, new_end_m, tzinfo=tz),
        )

    occurrence_windows = [_new_window(b) for b in future_bookings]
    # Cancelled occurrences are deliberately absent from the conflict check
    # below — a cancelled occurrence does not occupy the room, so testing it
    # against real bookings would reject valid edits.
    cancelled_windows = [_new_window(b) for b in future_cancelled]

    # ── Conflict check: exclude ALL series members ────────────────────────────
    all_conflict_rows: list[Booking] = []
    occurrence_conflicts: list[dict] = []

    for b, new_starts, new_ends in occurrence_windows:
        conflicts = await find_conflicts(
            db, effective_room_id, new_starts, new_ends,
            exclude_booking_ids=series_ids_set,
        )
        if conflicts:
            all_conflict_rows.extend(conflicts)
            occ_local_date = new_starts.astimezone(tz).date().isoformat()
            occurrence_conflicts.append({
                "date": occ_local_date,
                "conflicts": await _build_slim_out_list(db, conflicts),
            })

    if all_conflict_rows:
        # Deduplicate global conflicts
        seen_ids: set[uuid.UUID] = set()
        unique_conflicts: list[Booking] = []
        for c in all_conflict_rows:
            if c.id not in seen_ids:
                seen_ids.add(c.id)
                unique_conflicts.append(c)

        conflicts_slim = await _build_slim_out_list(db, unique_conflicts)

        # No suggestions for series edit — keep it simple
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": "conflict",
                "conflicts": [c.model_dump(mode="json") for c in conflicts_slim],
                "occurrence_conflicts": [
                    {
                        "date": oc["date"],
                        "conflicts": [c.model_dump(mode="json") for c in oc["conflicts"]],
                    }
                    for oc in occurrence_conflicts
                ],
                "suggestions": None,
            },
        )

    # ── Apply in one transaction ──────────────────────────────────────────────
    # Compute new ical_sequence = max(existing sequence across all series rows) + 1
    new_sequence = max(b.ical_sequence for b in all_series_bookings) + 1

    try:
        # Cancelled occurrences are updated alongside confirmed ones. The loop
        # never touches b.status, so they stay cancelled while their times track
        # the series — which is what keeps EXDATE aligned.
        for b, new_starts, new_ends in occurrence_windows + cancelled_windows:
            before_snapshot = {
                "id": str(b.id),
                "room_id": str(b.room_id),
                "title": b.title,
                "description": b.description,
                "organizer_id": str(b.organizer_id),
                "attendee_ids": b.attendee_ids,
                "starts_at": b.starts_at.isoformat(),
                "ends_at": b.ends_at.isoformat(),
                "status": b.status,
                "ical_sequence": b.ical_sequence,
                "sync_status": b.sync_status,
            }

            # Apply field updates
            if body.title is not None:
                b.title = body.title
            # C2: use model_fields_set to distinguish explicit null (clear) from absent
            if "description" in body.model_fields_set:
                b.description = body.description
            if body.attendee_ids is not None:
                b.attendee_ids = [str(aid) for aid in body.attendee_ids]
            # C4: only reassign room when caller explicitly requested a change
            if room_explicitly_changed:
                b.room_id = effective_room_id
            if body.start_time is not None:
                b.starts_at = new_starts
                b.ends_at = new_ends

            b.ical_sequence = new_sequence
            b.sync_status = "pending"

            await db.flush()

            after_snapshot = {
                "id": str(b.id),
                "room_id": str(b.room_id),
                "title": b.title,
                "description": b.description,
                "organizer_id": str(b.organizer_id),
                "attendee_ids": b.attendee_ids,
                "starts_at": b.starts_at.isoformat(),
                "ends_at": b.ends_at.isoformat(),
                "status": b.status,
                "ical_sequence": b.ical_sequence,
                "sync_status": b.sync_status,
            }

            audit = BookingAuditLog(
                booking_id=b.id,
                action="update",
                actor_id=actor_id,
                before=before_snapshot,
                after=after_snapshot,
            )
            db.add(audit)

        await db.flush()

    except IntegrityError:
        await db.rollback()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": "conflict",
                "conflicts": [],
                "occurrence_conflicts": [],
                "suggestions": None,
            },
        )

    # ── ONE notification for the first future occurrence ──────────────────────
    # Outlook rewrites past occurrences too on series updates — the DB keeps
    # historical rows unchanged. This is the accepted tradeoff for series edits.
    first_future = future_bookings[0]
    series_rrule = first_future.rrule
    try:
        await enqueue(db, [first_future], "updated", rrule=series_rrule)
    except Exception:
        logger.exception(
            "enqueue failed for series edit of series_id %s — suppressed",
            series_id,
        )

    return {"updated": len(future_bookings), "series_id": str(series_id)}


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /bookings/{id}
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/{booking_id}", response_model=BookingOut)
async def update_booking(
    booking_id: uuid.UUID,
    body: BookingUpdate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Modify a confirmed booking.

    Rules:
    - Organizer (JWT sub == organizer_id) OR admin (manage_meeting_rooms) else 403.
    - Booking must be confirmed else 400.
    - Already started (starts_at <= now) → 403 unless admin.
    - Series member (series_id not null) → 400 series_member_immutable.
    - If room_id/starts_at/ends_at changed: re-validate + conflict-check excluding self.
    - On success: ical_sequence += 1, sync_status = "pending", audit "update", enqueue "updated".
    """
    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)
    actor_id = uuid.UUID(current_user["sub"])
    is_admin = await is_booking_admin(current_user, db)

    # Fetch the booking with its room
    result = await db.execute(
        select(Booking, MeetingRoom)
        .join(MeetingRoom, Booking.room_id == MeetingRoom.id)
        .where(Booking.id == booking_id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    booking, room = row

    # Authorization: organizer or admin.
    # Return 404 (not 403) to prevent existence probing by unauthorized callers.
    if actor_id != booking.organizer_id and not is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    # Must be confirmed
    if booking.status != "confirmed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot modify booking with status '{booking.status}'",
        )

    # Series member → immutable
    if booking.series_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="series_member_immutable",
        )

    # Already started → 403 unless admin
    now = datetime.now(tz)
    if booking.starts_at <= now and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify a booking that has already started",
        )

    # Determine effective time/room (may be unchanged)
    new_starts_at = body.starts_at if body.starts_at is not None else booking.starts_at
    new_ends_at = body.ends_at if body.ends_at is not None else booking.ends_at
    new_room_id = body.room_id if body.room_id is not None else booking.room_id
    time_or_room_changed = (
        body.starts_at is not None
        or body.ends_at is not None
        or body.room_id is not None
    )

    # If room changed, load new room
    if body.room_id is not None and body.room_id != booking.room_id:
        new_room_result = await db.execute(
            select(MeetingRoom).where(MeetingRoom.id == body.room_id)
        )
        new_room = new_room_result.scalar_one_or_none()
        if new_room is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
        if new_room.status != "available":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Room is not bookable (status is not 'available')",
            )
    else:
        new_room = room
        # Re-validate room status even on a time-only change: the room may have
        # been put into maintenance/disabled AFTER the original booking was created.
        if new_room.status != "available":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Room is not bookable (status is not 'available')",
            )

    if time_or_room_changed:
        # Load config
        config = await get_or_create_config(db)
        cfg_rules: dict = config.rules or {}
        min_dur = cfg_rules.get("min_duration_minutes", 15)
        max_dur = cfg_rules.get("max_duration_minutes", 240)
        advance_days = cfg_rules.get("advance_days", 30)
        default_open_start = cfg_rules.get("default_open_start", "08:00")
        default_open_end = cfg_rules.get("default_open_end", "20:00")

        _validate_grid(new_starts_at, new_ends_at, tz)
        _validate_duration(new_starts_at, new_ends_at, min_dur, max_dur)
        _validate_open_hours(new_starts_at, new_ends_at, new_room, tz, default_open_start, default_open_end)
        # Only validate advance window if starts_at changed and not admin
        # (admin can push a meeting forward past its original time)
        if body.starts_at is not None and not is_admin:
            _validate_advance_window(new_starts_at, new_room, tz, advance_days)

        # Conflict check: exclude self
        conflicts = await find_conflicts(
            db, new_room_id, new_starts_at, new_ends_at,
            exclude_booking_ids={booking.id}
        )
        if conflicts:
            return await _build_conflict_response(
                db, new_room, new_starts_at, new_ends_at, conflicts,
                cfg_rules,
            )
    else:
        cfg_rules = {}

    # Snapshot before state
    before_snapshot = {
        "id": str(booking.id),
        "room_id": str(booking.room_id),
        "title": booking.title,
        "description": booking.description,
        "organizer_id": str(booking.organizer_id),
        "attendee_ids": booking.attendee_ids,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
        "status": booking.status,
        "ical_sequence": booking.ical_sequence,
        "sync_status": booking.sync_status,
    }

    # Apply updates
    if body.title is not None:
        booking.title = body.title
    if body.description is not None:
        booking.description = body.description
    if body.attendee_ids is not None:
        booking.attendee_ids = [str(aid) for aid in body.attendee_ids]
    if body.room_id is not None:
        booking.room_id = body.room_id
    if body.starts_at is not None:
        booking.starts_at = body.starts_at
    if body.ends_at is not None:
        booking.ends_at = body.ends_at

    booking.ical_sequence = booking.ical_sequence + 1
    booking.sync_status = "pending"

    await db.flush()

    # After snapshot
    after_snapshot = {
        "id": str(booking.id),
        "room_id": str(booking.room_id),
        "title": booking.title,
        "description": booking.description,
        "organizer_id": str(booking.organizer_id),
        "attendee_ids": booking.attendee_ids,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
        "status": booking.status,
        "ical_sequence": booking.ical_sequence,
        "sync_status": booking.sync_status,
    }

    audit = BookingAuditLog(
        booking_id=booking.id,
        action="update",
        actor_id=actor_id,
        before=before_snapshot,
        after=after_snapshot,
    )
    db.add(audit)
    await db.flush()

    try:
        await enqueue(db, [booking], "updated")
    except Exception:
        logger.exception("enqueue failed for update of booking %s — suppressed", booking.id)

    # Resolve organizer name for response
    organizer_name = await _resolve_organizer_name(db, booking.organizer_id)
    return _make_booking_out(booking, new_room, organizer_name)


# ─────────────────────────────────────────────────────────────────────────────
# POST /bookings/{id}/cancel
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{booking_id}/cancel")
async def cancel_booking(
    booking_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    series: bool = Query(default=False),
):
    """Cancel a booking (or a full series future-occurrence set).

    Query params:
        series: if True, cancel all future confirmed occurrences of the series.
                Requires the target booking to be a series member.
                If False (default), cancel only the target booking.  For a series
                member this is a single-occurrence exception: siblings are kept
                and the invite carries RECURRENCE-ID (no RRULE) so Outlook drops
                only this instance.

    Returns: {"cancelled": n}
    """
    actor_id = uuid.UUID(current_user["sub"])
    is_admin = await is_booking_admin(current_user, db)
    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)

    # Fetch the target booking
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    # Authorization: organizer or admin.
    # Return 404 (not 403) to prevent existence probing by unauthorized callers.
    if actor_id != booking.organizer_id and not is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    now = datetime.now(tz)

    if series:
        # series=true: cancel all future confirmed occurrences of the series
        if booking.series_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Booking is not a series member; series=true requires a series member",
            )

        # Fetch all future confirmed occurrences.
        # PRD §9.7: organizers can cancel meetings that have not yet started.
        # Occurrences already in-progress or in the past are intentionally excluded —
        # an in-progress meeting finishes naturally without being interrupted by the
        # series cancel, which is the expected product behaviour.
        future_result = await db.execute(
            select(Booking).where(
                Booking.series_id == booking.series_id,
                Booking.status == "confirmed",
                Booking.starts_at > now,
            )
        )
        future_bookings = list(future_result.scalars().all())

        cancelled_count = 0
        for b in future_bookings:
            b.status = "cancelled"
            b.sync_status = "pending"
            audit_action = "force_cancel" if (actor_id != b.organizer_id and is_admin) else "cancel"
            audit = BookingAuditLog(
                booking_id=b.id,
                action=audit_action,
                actor_id=actor_id,
                before={"status": "confirmed"},
                after={"status": "cancelled"},
            )
            db.add(audit)
            cancelled_count += 1

        await db.flush()

        # ONE notification covers the entire VEVENT cancellation
        if future_bookings:
            first_future = min(future_bookings, key=lambda b: b.starts_at)
            series_rrule = first_future.rrule
            try:
                await enqueue(db, [first_future], "cancelled", rrule=series_rrule)
            except Exception:
                logger.exception(
                    "enqueue failed for series cancel of series_id %s — suppressed",
                    booking.series_id,
                )

        return {"cancelled": cancelled_count}

    else:
        # series=false: cancel exactly one booking.
        # For a series member this is a single-occurrence exception — the row is
        # cancelled alone and the invite carries RECURRENCE-ID without an RRULE
        # (see notifications.send_notification), so Outlook drops only this
        # instance.
        if booking.status != "confirmed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel booking with status '{booking.status}'",
            )

        # The series branch only ever cancels occurrences with starts_at > now.
        # The single branch must match for series members, otherwise an admin
        # could cancel an occurrence that already happened and mail every
        # attendee about it.
        if booking.series_id is not None and booking.starts_at <= now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="occurrence_already_started",
            )

        booking.status = "cancelled"
        booking.sync_status = "pending"
        if booking.series_id is not None:
            # A RECURRENCE-ID instance carries its own SEQUENCE, independent of
            # the series master, so the row's own value is the right basis.
            booking.ical_sequence += 1

        audit_action = "force_cancel" if (actor_id != booking.organizer_id and is_admin) else "cancel"
        audit = BookingAuditLog(
            booking_id=booking.id,
            action=audit_action,
            actor_id=actor_id,
            before={"status": "confirmed"},
            after={"status": "cancelled"},
        )
        db.add(audit)
        await db.flush()

        # A series member cancels one instance ('cancelled_occ' → RECURRENCE-ID);
        # a standalone booking cancels the whole event.
        notif_type = "cancelled_occ" if booking.series_id is not None else "cancelled"
        try:
            await enqueue(db, [booking], notif_type)
        except Exception:
            logger.exception(
                "enqueue failed for cancel of booking %s — suppressed", booking.id
            )

        return {"cancelled": 1}
