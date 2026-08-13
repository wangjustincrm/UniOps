"""Planning parameters (key-value).

Deliberately generic: Phase 1C's raw_material_loss_rate / packaging_loss_rate
land in this same table rather than growing another one-row config table.
"""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MrpPlanningParam(Base):
    __tablename__ = "mrp_planning_params"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    # Any, not dict: week_calendar_mode stores a bare JSON string
    # ("iso_thursday"), and Phase 1C's loss-rate params will store numbers.
    # The table's whole point is being generic — the type hint should not
    # claim it only ever holds objects.
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
