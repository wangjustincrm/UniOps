"""QuickBooks Online mirror tables (finance owns the schema; migration 0027).

Read-only mirror of the QBO company — no association, no posting. Columns were
defined from real production payloads; anything unmapped stays in `raw`.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"


class QboSyncRun(UUIDPrimaryKey, TimestampMixin, Base):
    """One row per UI/CLI-triggered import; doubles as the live progress feed."""
    __tablename__ = "qbo_sync_runs"

    mode: Mapped[str] = mapped_column(String(15), nullable=False)          # full | incremental
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=RUNNING, index=True)
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    counters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    watermarks: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class QboAccount(TimestampMixin, Base):
    """Chart-of-accounts mirror. Master entity — no lines. `qbo_id` is the
    natural primary key (mirror rows don't need a separate UUID)."""
    __tablename__ = "qbo_accounts"

    qbo_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    sync_token: Mapped[str | None] = mapped_column(String(10))
    name: Mapped[str | None] = mapped_column(String(255))
    acct_num: Mapped[str | None] = mapped_column(String(50))
    account_type: Mapped[str | None] = mapped_column(String(64))
    account_sub_type: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(10))
    current_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    active: Mapped[bool | None] = mapped_column(Boolean)
    last_updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)


class _TxnHeaderMixin:
    """Shared transaction-header columns (Bill/BillPayment/VendorCredit/...)."""
    qbo_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    sync_token: Mapped[str | None] = mapped_column(String(10))
    doc_number: Mapped[str | None] = mapped_column(String(64))
    txn_date: Mapped[str | None] = mapped_column(String(10))
    due_date: Mapped[str | None] = mapped_column(String(10))
    currency: Mapped[str | None] = mapped_column(String(10))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    total_amt: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    home_total_amt: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    home_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    global_tax_calc: Mapped[str | None] = mapped_column(String(20))
    private_note: Mapped[str | None] = mapped_column(Text)
    counterparty_id: Mapped[str | None] = mapped_column(String(20))
    counterparty_name: Mapped[str | None] = mapped_column(String(255))
    last_updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)


class _TxnLineMixin:
    """Shared line columns."""
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_qbo_id: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    line_num: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    detail_type: Mapped[str | None] = mapped_column(String(48))
    account_id: Mapped[str | None] = mapped_column(String(20))
    account_name: Mapped[str | None] = mapped_column(String(255))
    tax_code_ref: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(Text)
    linked_txn_id: Mapped[str | None] = mapped_column(String(20))
    linked_txn_type: Mapped[str | None] = mapped_column(String(32))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)


class QboBill(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_bills"


class QboBillLine(_TxnLineMixin, Base):
    __tablename__ = "qbo_bill_lines"


class QboBillPayment(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_bill_payments"
    pay_type: Mapped[str | None] = mapped_column(String(20))


class QboBillPaymentLine(_TxnLineMixin, Base):
    __tablename__ = "qbo_bill_payment_lines"


class QboVendorCredit(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_vendor_credits"


class QboVendorCreditLine(_TxnLineMixin, Base):
    __tablename__ = "qbo_vendor_credit_lines"


class QboVendor(TimestampMixin, Base):
    """Vendor master mirror. Canadian slip flags (T4A/T5018 eligibility) are
    kept in `raw` only — no dedicated columns."""
    __tablename__ = "qbo_vendors"

    qbo_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    sync_token: Mapped[str | None] = mapped_column(String(10))
    display_name: Mapped[str | None] = mapped_column(String(255))
    print_on_check_name: Mapped[str | None] = mapped_column(String(255))
    currency: Mapped[str | None] = mapped_column(String(10))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    email: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool | None] = mapped_column(Boolean)
    last_updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
