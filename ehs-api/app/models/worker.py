"""Worker safety profile and the position/requirement matrix.

The profile is a 1:1 side table on `users`, not extra columns on it. Two
reasons, in order of weight:

1. `users` is read by ten services, each with its own partial mirror of the
   columns it cares about. Adding eight columns there puts every one of those
   mirrors into a "declared shape no longer matches the table" state — the
   same class of drift that left four services declaring document_type as
   varchar(10) after it had been widened to 20.
2. Emergency contacts and medical restrictions are PHIPA-sensitive. A side
   table gives `ehs.worker.medical.read` a physical boundary to enforce,
   instead of hoping every service that selects from `users` avoids the
   sensitive columns.
"""
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class WorkerProfile(TimestampMixin, Base):
    __tablename__ = "ehs_worker_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    # Not the same thing as users.erp_person_code — that is the NC65 person
    # record; this is the payroll/plant employee number people actually quote.
    employee_no: Mapped[str | None] = mapped_column(String(30), nullable=True, unique=True)
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    shift_code: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    phone_mobile: Mapped[str | None] = mapped_column(String(30), nullable=True)
    primary_location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    emergency_contact: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )
    # PHIPA-sensitive: restrictions relevant to return-to-work planning.
    medical_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_safety_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # member | certified_member | co_chair — 23 members, 20 of them certified.
    jhsc_role: Mapped[str | None] = mapped_column(String(20), nullable=True)


class JobPosition(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_job_positions"

    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PositionRequirement(UUIDPrimaryKey, TimestampMixin, Base):
    """What a position requires: a course, a PPE item, or a medical check."""

    __tablename__ = "ehs_position_requirements"

    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_job_positions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    requirement_type: Mapped[str] = mapped_column(String(20), nullable=False)  # training|ppe|medical
    # Points at a course for training requirements, or a vocabulary item for
    # PPE and medical ones.
    course_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    vocabulary_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class WorkerPosition(UUIDPrimaryKey, TimestampMixin, Base):
    """Which positions a worker holds, and when. Drives the training gap report."""

    __tablename__ = "ehs_worker_positions"
    __table_args__ = (
        UniqueConstraint("user_id", "position_id", "effective_from", name="uq_ehs_worker_position"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_job_positions.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
