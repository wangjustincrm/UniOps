"""Bank accounts, statement lines, FX rates — finance-api owns all (Phase a A3).

bank_transactions.amount is SIGNED (negative = outflow). Statement lines
reconcile against payment_records; reconciliation is event-free for now
(read directly), GL replay takes over later. No cross-service mirrors —
everything here is finance-owned.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

UNMATCHED = "unmatched"
MATCHED = "matched"
EXCLUDED = "excluded"


class BankAccount(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "bank_accounts"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(255), nullable=False)   # bank or card issuer
    # bank = cash asset account; credit_card = liability (Credit Card Payable)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="bank")
    account_masked: Mapped[str | None] = mapped_column(String(40), nullable=True)  # last 4
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    ledger_account_code: Mapped[str | None] = mapped_column(String(10), nullable=True)  # COA cash(bank) / liability(card)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # A3 workbench: reusable CSV column mapping for this account's statement export
    # {date, description, reference?, amount? | debit?+credit?, date_format?, default_year?}
    import_mapping: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Which NC bank account (BD_BANKACCSUB.CODE, e.g. '1033760' = RBC CAD) this
    # row IS. The book side of a reconciliation is GL 100201 filtered by this
    # code; unset means the account cannot be reconciled against the ledger.
    nc_bank_account_code: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class BankTransaction(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "bank_transactions"
    __table_args__ = (
        # prevents re-importing the same statement: hash of account|date|amount|desc|ref
        UniqueConstraint("import_hash", name="uq_bank_transactions_import_hash"),
    )

    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    txn_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)  # signed: -=outflow
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=UNMATCHED, index=True)
    matched_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_records.id", ondelete="SET NULL"), nullable=True,
    )
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    matched_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    import_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ExchangeRate(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "exchange_rates"
    __table_args__ = (
        UniqueConstraint("from_currency", "to_currency", "effective_date",
                         name="uq_exchange_rates_pair_date"),
    )

    from_currency: Mapped[str] = mapped_column(String(10), nullable=False)
    to_currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
