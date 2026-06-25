"""Fiscal periods — FIN-GL-002 soft/hard close (Phase 0-B1.7).

A period row is created on first close action; absence of a row means OPEN.
Phase 0 semantics: any non-open status blocks payment execution (the single
money-out gate). GL-stage will extend close semantics to journal posting.
"""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

OPEN = "open"
SOFT_CLOSED = "soft_closed"
HARD_CLOSED = "hard_closed"


class FiscalPeriod(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "fiscal_periods"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'soft_closed', 'hard_closed')",
                        name="ck_fiscal_periods_status"),
    )

    period: Mapped[str] = mapped_column(String(7), nullable=False, unique=True)  # 'YYYY-MM'
    # single-entity for now; multi-entity moves uniqueness to (entity_id, period)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default=OPEN)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
