"""First-aid register — Regulation 1101.

Every first-aid treatment is recorded, whether or not it becomes a reportable
incident. Kept as its own table rather than as a flavour of incident because
the regulation asks for a standing register that can be produced on request,
and because most entries never become incidents.

`incident_id` is nullable and filled in later: a treatment logged at the time
can be escalated once it turns out to be more than first aid.

Retention is at least five years, and the table is append-only in production
via a trigger.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class FirstAidLog(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_first_aid_log"

    log_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_incidents.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    location_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    injured_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    injured_name: Mapped[str] = mapped_column(String(255), nullable=False)
    first_aider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    first_aider_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    body_part_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    body_part_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    treatment_given: Mapped[str] = mapped_column(Text, nullable=False)
    sent_offsite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    follow_up: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
