import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DEFAULT_RULES = {
    "slot_minutes": 15,
    "min_duration_minutes": 15,
    "max_duration_minutes": 240,
    "advance_days": 30,
    "default_open_start": "08:00",
    "default_open_end": "20:00",
    "notify_room_admin": False,
    "room_admin_emails": [],
}


class BookingConfig(Base):
    __tablename__ = "booking_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    smtp_settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rules: Mapped[dict] = mapped_column(JSONB, nullable=False, default=lambda: dict(DEFAULT_RULES))
    organizer_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="system")  # system|initiator
