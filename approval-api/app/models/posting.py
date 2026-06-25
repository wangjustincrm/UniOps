"""Write-only mirror of finance-api's posting_events / posting_lines.

Schema is OWNED by finance-api (alembic 0002_posting_events). Do not migrate
these tables from approval-api. Mirrors follow this service's existing
pattern (pr.py, po.py, task.py are all mirrors of other services' tables).
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PostingEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "posting_events"
    __table_args__ = (
        UniqueConstraint("source_doc_type", "source_doc_id", "event_type",
                         name="uq_posting_events_source"),
    )

    source_service: Mapped[str] = mapped_column(String(20), nullable=False)
    source_doc_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_doc_number: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(String(7), nullable=True)


class PostingLine(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "posting_lines"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posting_events.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    line_role: Mapped[str] = mapped_column(String(30), nullable=False)
    account_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    partner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("1"))
    memo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
