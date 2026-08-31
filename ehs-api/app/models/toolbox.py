"""Toolbox talks: monthly topic, per-supervisor delivery, many signatures."""
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class ToolboxTalk(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_toolbox_talks"

    talk_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # First day of the month it belongs to, so a month is a value.
    period_month: Mapped[date] = mapped_column(Date, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                default=False, server_default="false")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class TbtDelivery(UUIDPrimaryKey, TimestampMixin, Base):
    """One supervisor delivering the talk to their team."""

    __tablename__ = "ehs_tbt_deliveries"
    __table_args__ = (UniqueConstraint("talk_id", "supervisor_id", name="uq_ehs_tbt_delivery"),)

    talk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_toolbox_talks.id", ondelete="CASCADE"), nullable=False)
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    supervisor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False,
                                         default="pending", server_default="pending", index=True)
    held_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_attendees: Mapped[int] = mapped_column(Integer, nullable=False,
                                                     default=0, server_default="0")
    signed_count: Mapped[int] = mapped_column(Integer, nullable=False,
                                               default=0, server_default="0")
    last_reminded_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class TbtSignature(UUIDPrimaryKey, Base):
    """One person confirming they were at the meeting.

    Many of these per delivery — the model that is new here. Taken on one device
    passed round after the talk, which is why they arrive within a minute of
    each other and why there is no per-person login.
    """

    __tablename__ = "ehs_tbt_signatures"

    delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_tbt_deliveries.id", ondelete="CASCADE"),
        nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    person_name: Mapped[str] = mapped_column(String(255), nullable=False)
    person_role: Mapped[str | None] = mapped_column(String(150), nullable=True)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    signed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")
