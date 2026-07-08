"""Pydantic schemas for MeetingRoom CRUD endpoints."""
import uuid
from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, Field


class RoomBase(BaseModel):
    name: str
    code: str
    campus: str | None = None
    building: str | None = None
    floor: str | None = None
    area: str | None = None
    capacity: int = Field(gt=0)
    equipment: list[str] = []
    room_type: str = "standard"
    open_time_start: time | None = None
    open_time_end: time | None = None
    advance_booking_days: int | None = None
    owner_department: str | None = None
    notes: str | None = None
    image_file_ids: list[uuid.UUID] = []


class RoomCreate(RoomBase): ...


class RoomUpdate(BaseModel):
    """All fields optional — only provided fields are applied."""
    name: str | None = None
    code: str | None = None
    campus: str | None = None
    building: str | None = None
    floor: str | None = None
    area: str | None = None
    capacity: int | None = Field(default=None, gt=0)
    equipment: list[str] | None = None
    room_type: str | None = None
    open_time_start: time | None = None
    open_time_end: time | None = None
    advance_booking_days: int | None = None
    owner_department: str | None = None
    notes: str | None = None
    image_file_ids: list[uuid.UUID] | None = None


class RoomOut(RoomBase):
    id: uuid.UUID
    status: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class StatusChangeIn(BaseModel):
    status: str = Field(pattern="^(available|disabled|maintenance)$")
    notes: str | None = None


class StatusChangeOut(BaseModel):
    room: RoomOut
    affected_future_bookings: int
