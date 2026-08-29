"""Every legal clock in the module, in one table.

MOL's 48 hours, WSIB's three business days, the JHSC's 21-day reply, annual
policy reviews, certificate expiries and the three-year SDS review are all the
same shape: something started, something is due, and either it was satisfied or
it was not. Modelling them once means one scanner, one escalation path, and one
compliance calendar — the calendar is a by-product rather than a separate build.

The deadline is stored as an absolute timestamp, computed in Python at the
moment the clock starts. It is deliberately not a generated column: the
business-day rules have to skip Ontario statutory holidays, which Postgres has
no knowledge of, while the 48-hour rule counts wall-clock hours. Expressing
both in one SQL expression would be unmaintainable and impossible to unit test,
and these are the calculations that must never be wrong.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class StatutoryDeadline(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_statutory_deadlines"
    __table_args__ = (
        # Partial index: the scanner only ever looks at what is still open, and
        # the satisfied rows accumulate for five years.
        Index(
            "ix_ehs_deadline_due_open", "due_at",
            postgresql_where="satisfied_at IS NULL",
        ),
        Index("ix_ehs_deadline_source", "source_type", "source_id"),
    )

    # Polymorphic source, no FK — the same pattern the shared `tasks` table has
    # used across six services for two years.
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(60), nullable=True)

    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    # Shown on screen beside the countdown so the obligation is auditable at a
    # glance: "OHSA s.51(1)", "Reg 1101 s.5".
    regulation_ref: Mapped[str | None] = mapped_column(String(60), nullable=True)
    clock_type: Mapped[str] = mapped_column(String(10), nullable=False)  # calendar|business

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    satisfied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    satisfied_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    satisfied_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The filed report / WSIB confirmation, held as evidence.
    evidence_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    evidence_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # 0 none, 1 warned at T-24h, 2 warned at T-4h, 3 overdue.
    escalation_level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0",
    )
