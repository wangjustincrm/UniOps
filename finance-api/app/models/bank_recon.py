"""Bank Reconciliation v2 — statements, payment advices, match groups, sessions.

The v1 model (bank_transactions.matched_payment_id, one nullable FK) could only
express one bank line ↔ one payment. Reality, measured against RBC CAD July 2026:

  - one statement line "Direct Deposits (PDS) service total 48,336.03" is one
    payment-advice PDF holding 18 vendor payments, each its own NC ledger line;
  - the 14 Jul line 118,269.09 is TWO advices (7.13 + 7.14) released a day apart;
  - one advice line (Ideal Supply 1,220.65) can itself be two ledger lines
    (629.69 + 590.96).

So the spine is a match GROUP: N bank lines ↔ M ledger lines, optionally explained
by a payment advice. The book side is NC's GL (journal_voucher_lines on account
100201, scoped by the bank_account auxiliary) — not payment_records, because
payroll, internal transfers, bank fees and head-office funding have no
payment_record and never will.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

# ── statement / advice lifecycle ───────────────────────────────────────────────
IMPORTED = "imported"
SUPERSEDED = "superseded"

# ── reconciliation lifecycle ───────────────────────────────────────────────────
OPEN = "open"
FINALIZED = "finalized"

# ── advice kinds (what the bank portal hands back) ─────────────────────────────
PDS_BATCH = "pds_batch"        # "Payment File Content" — a merged vendor batch
BILL_PAYMENT = "bill_payment"  # "Released Bill Payment Confirmation Numbers"

# ── how a match was made (ladder rung; see services/bank_matching.py) ──────────
M_ADVICE_TOTAL = "advice_total"        # 1 advice total  -> 1 bank line
M_ADVICE_COMBO = "advice_combo"        # 2-3 advice totals -> 1 bank line
M_CONFIRMATION = "confirmation_no"     # bill-payment confirmation number in the description
M_ADVICE_LINE = "advice_line"          # advice line -> ledger line (inside a group)
M_DIRECT = "direct"                    # 1 ledger line <-> 1 bank line, no batch
M_SUBSET_SUM = "subset_sum"            # bounded subset-sum inside one vendor bucket
M_MANUAL = "manual"                    # a human built the group


class BankStatement(UUIDPrimaryKey, TimestampMixin, Base):
    """One imported statement period for one bank account.

    `verified` is the gate that makes AI extraction safe: opening + Σ signed ==
    closing, every printed running balance reproduced in order, and the counts
    matching the statement's own "Total cheques & debits (N)". An unverified
    statement is kept (so the failure is inspectable) but must never be
    reconciled against.
    """
    __tablename__ = "bank_statements"
    __table_args__ = (
        # Re-importing a period supersedes the old row rather than duplicating it.
        Index("uq_bank_statements_live_period", "bank_account_id", "period_start",
              "period_end", unique=True, postgresql_where="status = 'imported'"),
        CheckConstraint("status in ('imported','superseded')",
                        name="ck_bank_statements_status"),
    )

    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")

    opening_balance: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    closing_balance: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    # As PRINTED on the statement — the anchors the verifier checks against, not
    # a re-derivation of our own line list.
    total_debits: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    total_credits: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    debit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    statement_account_no: Mapped[str | None] = mapped_column(String(60), nullable=True)

    source_storage_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_method: Mapped[str] = mapped_column(String(20), nullable=False, default="ai")
    parse_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    parsed_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verify_errors: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(String(12), nullable=False, default=IMPORTED, index=True)
    imported_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class BankPaymentAdvice(UUIDPrimaryKey, TimestampMixin, Base):
    """A payment file downloaded from the bank portal — the bridge that explodes
    one merged statement line back to vendor level.

    `tie_ok` is the deterministic parser's own check: Σ lines == the printed
    Total and line count == the printed "Number Of Payments". Measured 19/19 on
    the July set, which is why this layer costs no AI quota — the extractor is
    only reached when the tie fails.
    """
    __tablename__ = "bank_payment_advices"
    __table_args__ = (
        # The same file dragged in twice is the same advice.
        UniqueConstraint("bank_account_id", "source_sha256",
                         name="uq_bank_advices_account_sha"),
        CheckConstraint("advice_kind in ('pds_batch','bill_payment')",
                        name="ck_bank_advices_kind"),
        CheckConstraint("status in ('imported','superseded')",
                        name="ck_bank_advices_status"),
    )

    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True)
    advice_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    advice_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    client_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Bill payments only, and it is an exact join key: the statement description
    # reads "Bill payment - 8642 TYENDINAGA PROP".
    confirmation_number: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The figures as PRINTED, kept apart from our own sums so the tie test has
    # two independent sides.
    printed_total: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    printed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tie_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tie_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    source_storage_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_method: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    parsed_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(String(12), nullable=False, default=IMPORTED, index=True)
    imported_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class BankPaymentAdviceLine(UUIDPrimaryKey, TimestampMixin, Base):
    """One vendor payment inside an advice."""
    __tablename__ = "bank_payment_advice_lines"

    advice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_payment_advices.id", ondelete="CASCADE"),
        nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    payee_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payee_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    # Which NC ledger line this vendor payment is. SET NULL (never CASCADE): a
    # full nc_sync regenerates every jv_line_lines id, and a cascade would delete
    # the reconciliation work instead of orphaning it. The natural key below is
    # what the post-sync healer re-points from.
    matched_jv_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_voucher_lines.id", ondelete="SET NULL"),
        nullable=True, index=True)
    matched_nc_voucher_pk: Mapped[str | None] = mapped_column(String(40), nullable=True)
    matched_line_no: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BankReconciliation(UUIDPrimaryKey, TimestampMixin, Base):
    """One reconciliation period for one bank account — the artifact the auditors
    get. `difference` must be 0.00 to finalize; `snapshot` freezes the whole
    report so a later NC re-sync cannot change a signed-off period.
    """
    __tablename__ = "bank_reconciliations"
    __table_args__ = (
        UniqueConstraint("bank_account_id", "period_start", "period_end",
                         name="uq_bank_recon_account_period"),
        CheckConstraint("status in ('open','finalized')", name="ck_bank_recon_status"),
    )

    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")

    statement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_statements.id", ondelete="SET NULL"),
        nullable=True)

    statement_opening: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    statement_closing: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    book_opening: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    book_closing: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)

    cleared_debit_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    cleared_credit_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    cleared_debit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cleared_credit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outstanding_debit_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    outstanding_credit_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    difference: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))

    status: Mapped[str] = mapped_column(String(12), nullable=False, default=OPEN, index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    report_storage_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class BankReconMatch(UUIDPrimaryKey, TimestampMixin, Base):
    """A cleared group: N statement lines against M ledger lines.

    `method` records which rung of the ladder cleared it, so the report can show
    its own workings and a reviewer can tell an exact-amount hit from a
    subset-sum inference.
    """
    __tablename__ = "bank_recon_matches"

    reconciliation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_reconciliations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    # Which payment advice explains the grouping, when one does.
    advice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_payment_advices.id", ondelete="SET NULL"),
        nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BankReconMatchTxn(UUIDPrimaryKey, TimestampMixin, Base):
    """The bank side of a group. A statement line clears exactly once — hence the
    unique constraint, which is also what stops the ladder from double-clearing a
    line on two different rungs."""
    __tablename__ = "bank_recon_match_txns"
    __table_args__ = (
        UniqueConstraint("bank_transaction_id", name="uq_bank_recon_match_txn_once"),
    )

    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_recon_matches.id", ondelete="CASCADE"),
        nullable=False, index=True)
    bank_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_transactions.id", ondelete="CASCADE"),
        nullable=False)


class BankReconMatchBook(UUIDPrimaryKey, TimestampMixin, Base):
    """The ledger side of a group.

    Carries BOTH the surrogate `jv_line_id` and NC's natural key. A `full`
    nc_sync deletes every nc-sourced voucher and regenerates the line ids: the FK
    is SET NULL so the row survives, and heal_matches() re-points it from
    (nc_voucher_pk, line_no). Finalized periods are never re-pointed — their
    snapshot is the record.
    """
    __tablename__ = "bank_recon_match_books"

    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_recon_matches.id", ondelete="CASCADE"),
        nullable=False, index=True)
    jv_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_voucher_lines.id", ondelete="SET NULL"),
        nullable=True, index=True)
    nc_voucher_pk: Mapped[str | None] = mapped_column(String(40), nullable=True)
    line_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    account_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Signed, bank-statement convention: negative = money left the account. The
    # ledger stores debit/credit columns; this is the reconciled figure.
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
