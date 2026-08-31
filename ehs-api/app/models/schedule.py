"""Recurring inspections: the schedule, and each occurrence of it."""
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class FormSchedule(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_form_schedules"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_form_templates.id", ondelete="CASCADE"),
        nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    rotation: Mapped[str] = mapped_column(String(20), nullable=False,
                                          default="none", server_default="none")
    department_ids: Mapped[list] = mapped_column(JSONB, nullable=False,
                                                  default=list, server_default="[]")
    location_ids: Mapped[list] = mapped_column(JSONB, nullable=False,
                                                default=list, server_default="[]")
    # Stored rather than derived from a run count: deleting one occurrence must
    # not shift whose turn it is for every month afterwards.
    rotation_index: Mapped[int] = mapped_column(Integer, nullable=False,
                                                 default=0, server_default="0")

    assigned_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    lead_days: Mapped[int] = mapped_column(Integer, nullable=False,
                                            default=7, server_default="7")

    next_due_on: Mapped[date] = mapped_column(Date, nullable=False)
    last_generated_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                             default=True, server_default="true")


class FormScheduleRun(UUIDPrimaryKey, TimestampMixin, Base):
    """One occurrence. An overdue inspection is a row, not an absence."""

    __tablename__ = "ehs_form_schedule_runs"
    __table_args__ = (UniqueConstraint("schedule_id", "due_on",
                                       name="uq_ehs_schedule_run_due"),)

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_form_schedules.id", ondelete="CASCADE"),
        nullable=False)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_form_templates.id", ondelete="RESTRICT"),
        nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False,
                                         default="pending", server_default="pending")
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_form_submissions.id", ondelete="SET NULL"),
        nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
