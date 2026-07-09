"""Pydantic schemas for Booking endpoints.

BookingSlimOut is the read model used by:
  - GET /rooms/{id}  today_bookings / week_bookings
  - POST /bookings/precheck  conflicts list  (Task 6)

BookingCreate / BookingOut / BookingCreatedOut are the Task 7 write models.
"""
import re
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    series_truncated: True when the advance-booking window capped the series
        (i.e. expand_series dropped occurrences beyond now+advance_days).
        Always False for single bookings.
    """
    bookings: list[BookingOut]
    series_id: uuid.UUID | None
    series_truncated: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Task 8 schemas
# ─────────────────────────────────────────────────────────────────────────────

class BookingUpdate(BaseModel):
    """Request body for PATCH /bookings/{id}.

    All fields are optional — only provided fields are updated.
    If room_id, starts_at, or ends_at are changed, re-validation and
    conflict-check run against the new values.
    """
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    attendee_ids: list[uuid.UUID] | None = None
    room_id: uuid.UUID | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class SeriesUpdate(BaseModel):
    """Request body for PATCH /bookings/series/{series_id}.

    All fields optional — only provided fields are applied to future occurrences.

    start_time / end_time:
        Local time-of-day strings "HH:MM" (DISPLAY_TIMEZONE).
        Applied to every future occurrence's own calendar date.
        Both must be provided together or both omitted (both-or-neither rule).
    """
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    attendee_ids: Optional[list[uuid.UUID]] = None
    room_id: Optional[uuid.UUID] = None
    start_time: Optional[str] = None   # "HH:MM" local time
    end_time: Optional[str] = None     # "HH:MM" local time

    @model_validator(mode="after")
    def _times_both_or_neither(self) -> "SeriesUpdate":
        has_start = self.start_time is not None
        has_end = self.end_time is not None
        if has_start != has_end:
            raise ValueError("start_time and end_time must be provided together or both omitted")
        if has_start:
            _HHmm = re.compile(r"^\d{2}:\d{2}$")
            for label, val in [("start_time", self.start_time), ("end_time", self.end_time)]:
                if not _HHmm.match(val):  # type: ignore[arg-type]
                    raise ValueError(f"{label} must be in HH:MM format")
        return self


class BookingAdminOut(BookingOut):
    """BookingOut extended with organizer_name for admin list (no N+1).

    organizer_name is already on BookingOut; this alias exists so Tasks 10/14/15
    can import the distinct name and extend it further if needed.
    """
    pass


class AdminBookingListOut(BaseModel):
    """Response from GET /admin/bookings."""
    items: list[BookingAdminOut]
    total: int


# ─────────────────────────────────────────────────────────────────────────────
# Day-summary schemas  (GET /bookings/day)
# ─────────────────────────────────────────────────────────────────────────────

class DaySummaryRoom(BaseModel):
    """Room row in the day-summary response."""
    id: uuid.UUID
    name: str
    code: str
    floor: str | None
    area: str | None
    capacity: int
    status: str  # "available" | "maintenance" (disabled rooms excluded by query)

    model_config = ConfigDict(from_attributes=True)


class DaySummaryBookingOut(BookingSlimOut):
    """BookingSlimOut extended with room_id for the day-summary grid."""
    room_id: uuid.UUID

    model_config = ConfigDict(from_attributes=True)


class DaySummaryOut(BaseModel):
    """Response from GET /bookings/day."""
    date: str                       # "YYYY-MM-DD"
    open_start: str                 # "HH:MM"  from config defaults
    open_end: str                   # "HH:MM"
    timezone: str                   # IANA name, e.g. "America/Toronto" (= DISPLAY_TIMEZONE)
    rooms: list[DaySummaryRoom]     # non-disabled rooms, sorted floor then name
    bookings: list[DaySummaryBookingOut]  # confirmed bookings in the day window
