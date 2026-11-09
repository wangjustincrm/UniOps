"""Production equipment, and where it lives.

Separate from `ehs_safety_assets`: that one is extinguishers and eyewash
stations, inspected on a cycle; this one is machinery that appears on incident
forms. Different maintainers, different lifecycle.
"""
import uuid

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Equipment(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_equipment"

    asset_no: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ★ Legacy free text, kept in step with the System's label on every write
    # so a downgrade still reads. Nothing displays it: the System is read by id.
    equipment_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Round 12 (EQ-01/EQ-03): "Rename kind to be System … make the system a
    # pick list editable in the settings/lists." An entry of the
    # `equipment_system` list, and — a deliberate exception to the module's
    # label-snapshot rule — shown by its *current* label. HSE merge and rename
    # these on 7,000 machines; a snapshot would leave every row saying the old
    # name. A machine is reference data, not a signed record.
    system_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_vocabulary_items.id", ondelete="RESTRICT"),
        nullable=True, index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(150), nullable=True)
    model: Mapped[str | None] = mapped_column(String(150), nullable=True)
    serial_no: Mapped[str | None] = mapped_column(String(100), nullable=True)

    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                           nullable=True, index=True)
    location_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # False = retired (round 12, Q3): out of every picker, still named by the
    # records that already name it. Only a machine nothing names can be deleted.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                             default=True, server_default="true")
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
