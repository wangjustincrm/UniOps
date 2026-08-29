"""Return-to-work planning after a lost-time injury."""
import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class RtwPlan(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_rtw_plans"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_incidents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    worker_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    worker_name: Mapped[str] = mapped_column(String(255), nullable=False)
    functional_abilities: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )
    restrictions: Mapped[str | None] = mapped_column(Text, nullable=True)
    modified_duties: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whether the modified duties are at the worker's regular pay bears on
    # whether the absence counts as lost time — see ehs_config.lost_time_rules.
    at_regular_pay: Mapped[bool | None] = mapped_column(nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class RtwCheckin(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_rtw_checkins"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_rtw_plans.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    checkin_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
