"""Statutory training records and certifications.

Phase 1 covers the register and the expiry control — what an inspector asks
for. Course delivery, quizzes and toolbox talks come later.

Expiry is not policed by a scanner of its own. A certification with an expiry
date writes a row into `ehs_statutory_deadlines` with kind='cert_expiry', so
it is chased by the same loop, escalates the same way and shows up on the same
compliance calendar as the MOL and WSIB clocks.
"""
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Course(UUIDPrimaryKey, TimestampMixin, Base):
    """Training course catalogue.

    Seeded with the eight courses the HSE Manager confirmed: WHMIS, Lockout
    Tagout, Worker and Supervisor Safety Awareness and Working at Heights for
    all staff; Forklift, Mobile Equipment Work Platform, Confined Space and
    First Aid for selected staff.
    """

    __tablename__ = "ehs_courses"

    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Required by regulation rather than by internal policy — drives the
    # compliance percentage an inspector is shown.
    is_statutory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    # Applies to everyone, versus only to the positions that require it.
    applies_to_all: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Null means the training does not expire.
    validity_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TrainingRecord(UUIDPrimaryKey, TimestampMixin, Base):
    """One person completing one course. Append-only in production."""

    __tablename__ = "ehs_training_records"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_courses.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    course_label: Mapped[str] = mapped_column(String(200), nullable=False)
    completed_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    delivery: Mapped[str] = mapped_column(String(15), nullable=False, default="internal")
    certificate_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    recorded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class WorkerCertification(UUIDPrimaryKey, TimestampMixin, Base):
    """A licence or certificate held by a worker, with its expiry.

    `is_blocking` is what makes an expired forklift licence more than a
    reporting line: when set, assignment to work requiring it is refused.
    """

    __tablename__ = "ehs_worker_certifications"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    cert_type_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    cert_type_label: Mapped[str] = mapped_column(String(200), nullable=False)
    cert_no: Mapped[str | None] = mapped_column(String(80), nullable=True)
    issuer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
