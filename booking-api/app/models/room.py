import uuid
from datetime import datetime, time

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, Time, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MeetingRoom(Base):
    __tablename__ = "meeting_rooms"
    __table_args__ = (CheckConstraint("capacity > 0", name="ck_room_capacity_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    campus: Mapped[str | None] = mapped_column(String(128))
    building: Mapped[str | None] = mapped_column(String(128))
    floor: Mapped[str | None] = mapped_column(String(64))
    area: Mapped[str | None] = mapped_column(String(128))
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    equipment: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)   # tv|projector|whiteboard|video_conf|phone_conf
    room_type: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")  # standard|training|boardroom|multi_function
    open_time_start: Mapped[time | None] = mapped_column(Time)  # None → use config default
    open_time_end: Mapped[time | None] = mapped_column(Time)
    advance_booking_days: Mapped[int | None] = mapped_column(Integer)  # None → config default
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="available")  # available|disabled|maintenance
    owner_department: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    image_file_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
