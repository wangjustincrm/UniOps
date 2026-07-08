"""POST /bookings and GET /bookings/mine endpoints.

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
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import SessionDep
from app.core.permissions import CurrentUser
from app.crud.booking import create_booking_records
from app.crud.config import get_or_create_config
from app.models.booking import Booking
from app.models.room import MeetingRoom
from app.models.user_mirror import User
from app.schemas.booking import BookingCreate, BookingCreatedOut, BookingOut, BookingSlimOut
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
        rrule = build_rrule_string(spec, until_fallback=body.ends_at.astimezone(tz).date())
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
