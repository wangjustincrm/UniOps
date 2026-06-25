"""Posting events — the accounting spine (Phase 0-B1).

finance-api OWNS these tables (alembic 0002). approval-api and expense-api
hold thin mirrors and INSERT within their own transactions. No cross-service
FKs by design: partner_id / cost_center_id are bare UUIDs.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String,
    UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PostingEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "posting_events"
    __table_args__ = (
        UniqueConstraint("source_doc_type", "source_doc_id", "event_type",
                         name="uq_posting_events_source"),
        Index("ix_posting_events_doc", "source_doc_type", "source_doc_id"),
    )

    source_service: Mapped[str] = mapped_column(String(20), nullable=False)
    source_doc_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_doc_number: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(String(7), nullable=True, index=True)  # 'YYYY-MM'


class PostingLine(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "posting_lines"
    __table_args__ = (
        CheckConstraint("NOT (debit > 0 AND credit > 0)", name="ck_posting_lines_one_side"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posting_events.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    line_role: Mapped[str] = mapped_column(String(30), nullable=False)
    account_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    partner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, server_default=text("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, server_default=text("0"))
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, server_default="CAD")
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default=text("1"))
    memo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # FIN-GL-003 dimensions (B1.7) — bare UUIDs, no cross-service FKs by design
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class PostingLineDimension(UUIDPrimaryKey, TimestampMixin, Base):
    """Long-tail auxiliary dimensions (A1.7) for posting lines whose dim type
    has storage='aux_table' (income_expense_item / sales_type / country_region /
    bank_account / custom). High-frequency dims stay as PostingLine columns."""
    __tablename__ = "posting_line_dimensions"

    posting_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posting_lines.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    dim_code: Mapped[str] = mapped_column(String(40), nullable=False)
    value_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
