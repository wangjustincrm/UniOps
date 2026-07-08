"""Pydantic schemas for Booking endpoints.

BookingSlimOut is the read model used by:
  - GET /rooms/{id}  today_bookings / week_bookings
  - POST /bookings/precheck  conflicts list  (Task 6)
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
