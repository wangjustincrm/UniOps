"""Pydantic schemas for Booking endpoints.

BookingSlimOut is the read model used by:
  - GET /rooms/{id}  today_bookings / week_bookings
  - POST /bookings/precheck  conflicts list  (Task 6)

BookingCreate / BookingOut / BookingCreatedOut are the Task 7 write models.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.recurrence import SeriesSpec  # noqa: F401 — re-exported for Tasks 8/10/14/15


class BookingSlimOut(BaseModel):
    """Minimal booking read model — visible to all authenticated staff.

    Per PRD internal-transparency rule: title and organizer are shown to
    every employee so they can see the room's day schedule.
    """
    id: uuid.UUID
    title: str
    starts_at: datetime
    ends_at: datetime
    organizer_name: str

    model_config = ConfigDict(from_attributes=True)


# ─────────────────────────────────────────────────────────────────────────────
# Task 7 write schema
# ─────────────────────────────────────────────────────────────────────────────

class BookingCreate(BaseModel):
    """Request body for POST /bookings."""
    room_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    attendee_ids: list[uuid.UUID] = []
    starts_at: datetime
    ends_at: datetime
    needs_video_conf: bool = False
    series: SeriesSpec | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Task 7 read schema
# ─────────────────────────────────────────────────────────────────────────────

class BookingOut(BaseModel):
    """Full booking read model returned from POST /bookings and GET /bookings/mine.

    Extends BookingSlimOut fields plus room summary, series metadata, and
    sync/calendar fields.

    NOTE: needs_video_conf is NOT persisted (no column on Booking model).
    It is a write-only request field used only for equipment validation.
    """
    id: uuid.UUID
    title: str
    description: str | None
    starts_at: datetime
    ends_at: datetime
    organizer_name: str

    # Room summary (resolved at query time via join)
    room_id: uuid.UUID
    room_name: str
    room_code: str

    # Booking state
    status: str
    attendee_ids: list[uuid.UUID]

    # Series / calendar
    series_id: uuid.UUID | None
    rrule: str | None
    sync_status: str
    ical_sequence: int

    model_config = ConfigDict(from_attributes=True)


class BookingCreatedOut(BaseModel):
    """Response from POST /bookings.

    bookings: all created occurrence rows (1 for single, N for series).
    series_id: shared UUID for series; None for single bookings.
    """
    bookings: list[BookingOut]
    series_id: uuid.UUID | None
