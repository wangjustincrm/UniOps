"""Corrective and preventive actions — one register, whatever raised them.

Actions arrive from incidents, inspections, JHSC recommendations, audits,
observations and risk assessments. Splitting them per source would mean every
"what is outstanding" query UNIONs six tables and the overdue scanner is
written six times. One table with a polymorphic source keeps the queries
single and the escalation logic in one place; the shared `tasks` table has run
on the same pattern across six services for two years.

The guardrails that make the missing foreign key acceptable: `source_type` is
constrained to a fixed vocabulary, and Safety records are never hard-deleted,
so a dangling source cannot arise in normal operation.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, SmallInteger, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.common import EhsCommonDims

_SOURCE_TYPES = (
    "incident", "inspection", "jhsc", "audit", "observation",
    "hazard", "drill", "permit", "manual",
)


class Action(UUIDPrimaryKey, EhsCommonDims, TimestampMixin, Base):
    __tablename__ = "ehs_actions"
    __default_status__ = "open"
    __table_args__ = (
        CheckConstraint(
            "source_type IN (" + ", ".join(f"'{s}'" for s in _SOURCE_TYPES) + ")",
            name="ck_ehs_action_source_type",
        ),
    )

    action_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)

    source_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # Document number of the source, copied in so the list can show where an
    # action came from without joining across six possible tables.
    source_ref: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # The specific root cause this action addresses, when it came from an
    # investigation. Null for actions raised from anything else.
    cause_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_incident_causes.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    action_type: Mapped[str] = mapped_column(String(15), nullable=False, default="corrective")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Elimination / substitution / engineering / administrative / PPE. A COR
    # audit asks which level of control was applied, so it is a column rather
    # than something to infer from the description.
    hierarchy_of_control: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="normal")

    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # open -> in_progress -> pending_verification -> closed | cancelled
    # (`status` and its default come from EhsCommonDims / __default_status__.)
    # 0 none, 1 reminded, 2 supervisor notified, 3 HSE Manager notified.
    escalation_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    last_escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionUpdate(UUIDPrimaryKey, TimestampMixin, Base):
    """Append-only progress notes and evidence."""

    __tablename__ = "ehs_action_updates"

    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_actions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    file_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    new_status: Mapped[str | None] = mapped_column(String(20), nullable=True)


class ActionVerification(UUIDPrimaryKey, TimestampMixin, Base):
    """Confirmation by a second person that the control actually works.

    Distinct from marking the work done, deliberately. COR asks for evidence
    that a corrective action was effective, not merely completed, so `is_effective`
    can be false — a verification that fails reopens the action rather than
    closing it.
    """

    __tablename__ = "ehs_action_verifications"

    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_actions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    verified_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_effective: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
