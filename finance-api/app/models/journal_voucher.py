"""Journal vouchers (总账凭证) — formal GL vouchers over the posting spine.

One posting_event → one draft JV (generated in the same transaction). JV becomes
the GL source of truth once posted. Holds dual-currency (orig + local/CAD) and
quantity so it also contains imported NC65 history.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.posting import PostingEvent  # noqa: F401 — registers posting_events in metadata

DRAFT = "draft"
REVIEWED = "reviewed"
POSTED = "posted"
REVERSED = "reversed"


class JournalVoucher(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "journal_vouchers"
    __table_args__ = (
        UniqueConstraint("posting_event_id", name="uq_journal_vouchers_event"),
        CheckConstraint("status in ('draft','reviewed','posted','reversed')",
                        name="ck_journal_vouchers_status"),
    )

    jv_number: Mapped[str] = mapped_column(String(40), nullable=False)
    voucher_word: Mapped[str] = mapped_column(String(10), nullable=False, default="JV")
    voucher_date: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=DRAFT, index=True)

    posting_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posting_events.id", ondelete="SET NULL"), nullable=True)
    source_service: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_doc_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_doc_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    prepared_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reverses_jv_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reversed_by_jv_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    total_debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_local_debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_local_credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))

    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    nc_source_pk: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # NC GL_VOUCHER.PK_SYSTEM (GL/AP/AR/FA/CM/IA/EGL/OT/PLCF); null for go-forward JVs.
    source_subsystem: Mapped[str | None] = mapped_column(String(10), nullable=True)


class JournalVoucherLine(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "journal_voucher_lines"
    __table_args__ = (
        CheckConstraint("NOT (orig_debit > 0 AND orig_credit > 0)", name="ck_jv_lines_one_side"),
    )

    jv_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_vouchers.id", ondelete="CASCADE"),
        nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    account_code: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)

    orig_debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    orig_credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    local_debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    local_credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("1"))

    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    nc_cc_code: Mapped[str | None] = mapped_column(String(20), nullable=True)  # raw NC cost-center code (re-map safety)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    partner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    income_expense_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True)  # 收支项目提列 (multi-dim expand)


class JvLineDimension(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "jv_line_dimensions"

    jv_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_voucher_lines.id", ondelete="CASCADE"),
        nullable=False, index=True)
    dim_code: Mapped[str] = mapped_column(String(40), nullable=False)
    value_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
