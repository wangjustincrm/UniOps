"""Visitor ORM model — represents a person who visits the facility.

Per VMS PRD §5.2. Single Visitor row can have multiple Visit rows over time.

Training + PPE compliance is tracked at this level (not on Visit) because
frequent visitors shouldn't re-train every appointment. Freshness window
is 12 months (see `services/compliance.py`).
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class VisitorType(str, enum.Enum):
    supplier = "supplier"
    contractor = "contractor"
    inspector = "inspector"
    auditor = "auditor"
    customer = "customer"
    interviewee = "interviewee"
    other = "other"


class Visitor(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "vms_visitors"

    first_name:   Mapped[str] = mapped_column(String(100), nullable=False)
    last_name:    Mapped[str] = mapped_column(String(100), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    job_title:    Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone:        Mapped[str | None] = mapped_column(String(20), nullable=True)
    email:        Mapped[str | None] = mapped_column(String(200), nullable=True)
    visitor_type: Mapped[VisitorType] = mapped_column(
        SAEnum(VisitorType, name="vms_visitor_type"),
        nullable=False,
        default=VisitorType.other,
    )
    id_verified:  Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Per-visitor compliance — refreshed at most once every 12 months.
    safety_training_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    safety_training_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    ppe_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    ppe_issued_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
