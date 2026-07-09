import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        Index("ix_bookings_room_start", "room_id", "starts_at"),
        Index("ix_bookings_series", "series_id"),
        Index("ix_bookings_organizer", "organizer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("meeting_rooms.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    organizer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attendee_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="confirmed")  # confirmed|cancelled
    series_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rrule: Mapped[str | None] = mapped_column(Text)
    calendar_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    ical_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sync_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|sent|failed|compensating
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
