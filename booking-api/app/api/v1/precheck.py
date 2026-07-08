"""POST /bookings/precheck — conflict detection + room/time recommendation.

Produces:
    {
        "conflicts": [BookingSlimOut],
        "occurrence_conflicts": [],           # Task 7 wires series expansion here
        "suggestions": SuggestOut | null,     # non-null ONLY when conflicts exist
    }

Route is registered under /bookings in the api_router so the literal path
/bookings/precheck is resolved before any /bookings/{id} pattern (safe because
precheck is a static path segment, not a UUID).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select

from app.core.deps import SessionDep
from app.core.permissions import CurrentUser
from app.crud.config import get_or_create_config
from app.models.room import MeetingRoom
from app.models.user_mirror import User
from app.schemas.booking import BookingSlimOut
from app.schemas.room import RoomWithStatusOut
from app.services.recommend import find_conflicts, suggest

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Request / response schemas
# ─────────────────────────────────────────────────────────────────────────────

class SeriesSpec(BaseModel):
    """Recurring series parameters (Task 7 wires expansion logic)."""
    freq: str  # "daily" | "weekly"
    interval: int = 1
    count: int | None = None
    until: date | None = None


class PrecheckIn(BaseModel):
    room_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    attendee_count: int | None = None
    equipment: list[str] = []
    series: SeriesSpec | None = None

    @model_validator(mode="after")
    def _validate_window(self) -> "PrecheckIn":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class SuggestOut(BaseModel):
    nearest_slots: list[dict]        # [{"starts_at": dt, "ends_at": dt}]
    alternative_rooms: list[RoomWithStatusOut]


class PrecheckOut(BaseModel):
    conflicts: list[BookingSlimOut]
    occurrence_conflicts: list       # always [] until Task 7
    suggestions: SuggestOut | None


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/precheck", response_model=PrecheckOut, status_code=status.HTTP_200_OK)
async def precheck_booking(
    body: PrecheckIn,
    db: SessionDep,
    _user: CurrentUser,
):
    """Check whether a proposed booking has conflicts and return suggestions if so.

    - 404 if room_id unknown.
    - 422 if ends_at <= starts_at (handled by Pydantic validator).
    - suggestions is non-null ONLY when conflicts exist.
    - occurrence_conflicts is always [] (Task 7 wires series expansion here).
    """
    # ── Fetch room ────────────────────────────────────────────────────────────
    result = await db.execute(select(MeetingRoom).where(MeetingRoom.id == body.room_id))
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    # ── Find conflicts ────────────────────────────────────────────────────────
    conflict_rows = await find_conflicts(db, body.room_id, body.starts_at, body.ends_at)

    # Resolve organizer names for conflict slim models
    organizer_ids = list({b.organizer_id for b in conflict_rows})
    organizer_names: dict[uuid.UUID, str] = {}
    if organizer_ids:
        user_result = await db.execute(
            select(User.id, User.full_name).where(User.id.in_(organizer_ids))
        )
        organizer_names = {row.id: row.full_name for row in user_result}

    conflicts_out = [
        BookingSlimOut(
            id=b.id,
            title=b.title,
            starts_at=b.starts_at,
            ends_at=b.ends_at,
            organizer_name=organizer_names.get(b.organizer_id, "Unknown"),
        )
        for b in conflict_rows
    ]

    # ── Suggestions (only when conflicts exist) ───────────────────────────────
    suggestions_out: SuggestOut | None = None
    if conflict_rows:
        config = await get_or_create_config(db)
        cfg_rules: dict = config.rules or {}

        suggest_result = await suggest(
            db,
            room=room,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            attendee_count=body.attendee_count,
            equipment=body.equipment,
            cfg_rules=cfg_rules,
        )
        suggestions_out = SuggestOut(
            nearest_slots=suggest_result["nearest_slots"],
            alternative_rooms=suggest_result["alternative_rooms"],
        )

    # ── Task 7 wires series expansion here ───────────────────────────────────
    # occurrence_conflicts: list = []  (series expansion not yet implemented)

    return PrecheckOut(
        conflicts=conflicts_out,
        occurrence_conflicts=[],
        suggestions=suggestions_out,
    )
