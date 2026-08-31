"""Production equipment, and where it lives.

Separate from `ehs_safety_assets`: that one is extinguishers and eyewash
stations, inspected on a cycle; this one is machinery that appears on incident
forms. Different maintainers, different lifecycle.
"""
import uuid

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Equipment(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_equipment"

    asset_no: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    equipment_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(150), nullable=True)
    model: Mapped[str | None] = mapped_column(String(150), nullable=True)
    serial_no: Mapped[str | None] = mapped_column(String(100), nullable=True)

    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                           nullable=True, index=True)
    location_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                             default=True, server_default="true")
