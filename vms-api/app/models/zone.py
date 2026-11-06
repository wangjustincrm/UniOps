"""Access zones — the areas a visit can be booked for, defined in VMS Admin.

They used to be the hard-coded `AccessArea` enum. Each zone now carries its
own rules:

  - `gmp_grade`: a passing health declaration for every visitor before the
    badge prints, training / PPE confirmation at check-in, and the zone counts
    in the GMP / Lab reports;
  - its managers (`vms_zone_managers`): who approves at the workflow's Zone
    Manager step. A zone with no managers needs no approval at all — the visit
    is confirmed on booking.

`vms_visits.access_area` holds the zone's `code`, which never changes once the
zone exists (visits, reports and the approval title all key on it). Retire a
zone with `is_active = false` rather than deleting it.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Zone(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "vms_zones"

    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    gmp_grade: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )


class ZoneManager(Base):
    """One row per (zone, manager). Any one of a zone's managers may approve."""
    __tablename__ = "vms_zone_managers"

    zone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vms_zones.id", ondelete="CASCADE"), primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
