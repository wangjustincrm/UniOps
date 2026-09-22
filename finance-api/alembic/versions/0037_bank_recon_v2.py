"""Bank Reconciliation v2: statements, payment advices, match groups, sessions

Replaces the v1 one-to-one spine (bank_transactions.matched_payment_id) with
match GROUPS — N statement lines against M NC ledger lines, optionally explained
by a payment advice. matched_payment_id itself is kept: drilling from a bank line
to the PA/PO stays useful, it just stops being the reconciliation spine.

Note the two SET NULL foreign keys onto journal_voucher_lines. A `full` nc_sync
does `delete from journal_vouchers where nc_source_pk is not null` and
regenerates every line id; CASCADE there would silently delete a month of
reconciliation work. SET NULL orphans the row instead, and the natural key
(nc_voucher_pk, line_no) is what re-points it.

Revision ID: 0037_bank_recon_v2
Revises: 0036_nc_bank_acct_dim
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0037_bank_recon_v2"
down_revision = "0036_nc_bank_acct_dim"
branch_labels = None
depends_on = None

_TS = (
    sa.Column("created_at", sa.DateTime(timezone=True),
              server_default=sa.text("now()"), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True),
              server_default=sa.text("now()"), nullable=False),
)


def _uuid(name, *args, **kw):
    """A uuid column; extra positional args are passed through, so a ForeignKey
    can be given inline the way sa.Column takes it."""
    return sa.Column(name, postgresql.UUID(as_uuid=True), *args, **kw)


def upgrade() -> None:
    # ── statements ─────────────────────────────────────────────────────────────
    op.create_table(
        "bank_statements",
        _uuid("id", primary_key=True),
        _uuid("bank_account_id",
              sa.ForeignKey("bank_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("opening_balance", sa.Numeric(15, 2), nullable=False),
        sa.Column("closing_balance", sa.Numeric(15, 2), nullable=False),
        sa.Column("total_debits", sa.Numeric(15, 2), nullable=True),
        sa.Column("total_credits", sa.Numeric(15, 2), nullable=True),
        sa.Column("debit_count", sa.Integer(), nullable=True),
        sa.Column("credit_count", sa.Integer(), nullable=True),
        sa.Column("statement_account_no", sa.String(60), nullable=True),
        _uuid("source_storage_key", nullable=True),
        sa.Column("source_filename", sa.String(255), nullable=True),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("parse_method", sa.String(20), nullable=False, server_default="ai"),
        sa.Column("parse_model", sa.String(60), nullable=True),
        sa.Column("parsed_payload", postgresql.JSONB(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("verify_errors", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="imported"),
        _uuid("imported_by", nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        _uuid("entity_id", nullable=True),
        *_TS,
        sa.CheckConstraint("status in ('imported','superseded')",
                           name="ck_bank_statements_status"),
    )
    op.create_index("ix_bank_statements_bank_account_id", "bank_statements", ["bank_account_id"])
    op.create_index("ix_bank_statements_status", "bank_statements", ["status"])
    # One live statement per account+period; superseded rows pile up behind it.
    op.create_index("uq_bank_statements_live_period", "bank_statements",
                    ["bank_account_id", "period_start", "period_end"],
                    unique=True, postgresql_where=sa.text("status = 'imported'"))

    # ── payment advices ────────────────────────────────────────────────────────
    op.create_table(
        "bank_payment_advices",
        _uuid("id", primary_key=True),
        _uuid("bank_account_id",
              sa.ForeignKey("bank_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("advice_kind", sa.String(20), nullable=False),
        sa.Column("advice_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("client_number", sa.String(60), nullable=True),
        sa.Column("confirmation_number", sa.String(40), nullable=True),
        sa.Column("total", sa.Numeric(15, 2), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("printed_total", sa.Numeric(15, 2), nullable=True),
        sa.Column("printed_count", sa.Integer(), nullable=True),
        sa.Column("tie_ok", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("tie_error", sa.String(500), nullable=True),
        _uuid("source_storage_key", nullable=True),
        sa.Column("source_filename", sa.String(255), nullable=True),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("parse_method", sa.String(20), nullable=False, server_default="text"),
        sa.Column("parsed_payload", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="imported"),
        _uuid("imported_by", nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        _uuid("entity_id", nullable=True),
        *_TS,
        sa.UniqueConstraint("bank_account_id", "source_sha256",
                            name="uq_bank_advices_account_sha"),
        sa.CheckConstraint("advice_kind in ('pds_batch','bill_payment')",
                           name="ck_bank_advices_kind"),
        sa.CheckConstraint("status in ('imported','superseded')",
                           name="ck_bank_advices_status"),
    )
    op.create_index("ix_bank_advices_bank_account_id", "bank_payment_advices", ["bank_account_id"])
    op.create_index("ix_bank_advices_advice_date", "bank_payment_advices", ["advice_date"])
    op.create_index("ix_bank_advices_confirmation_number", "bank_payment_advices",
                    ["confirmation_number"])
    op.create_index("ix_bank_advices_status", "bank_payment_advices", ["status"])

    op.create_table(
        "bank_payment_advice_lines",
        _uuid("id", primary_key=True),
        _uuid("advice_id",
              sa.ForeignKey("bank_payment_advices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("payee_code", sa.String(120), nullable=True),
        sa.Column("payee_name", sa.String(255), nullable=True),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        # SET NULL, never CASCADE — see the module docstring.
        _uuid("matched_jv_line_id",
              sa.ForeignKey("journal_voucher_lines.id", ondelete="SET NULL"), nullable=True),
        sa.Column("matched_nc_voucher_pk", sa.String(40), nullable=True),
        sa.Column("matched_line_no", sa.Integer(), nullable=True),
        *_TS,
    )
    op.create_index("ix_bank_advice_lines_advice_id", "bank_payment_advice_lines", ["advice_id"])
    op.create_index("ix_bank_advice_lines_matched_jv_line_id", "bank_payment_advice_lines",
                    ["matched_jv_line_id"])

    # ── reconciliation sessions ────────────────────────────────────────────────
    op.create_table(
        "bank_reconciliations",
        _uuid("id", primary_key=True),
        _uuid("bank_account_id",
              sa.ForeignKey("bank_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        _uuid("statement_id",
              sa.ForeignKey("bank_statements.id", ondelete="SET NULL"), nullable=True),
        sa.Column("statement_opening", sa.Numeric(15, 2), nullable=True),
        sa.Column("statement_closing", sa.Numeric(15, 2), nullable=True),
        sa.Column("book_opening", sa.Numeric(15, 2), nullable=True),
        sa.Column("book_closing", sa.Numeric(15, 2), nullable=True),
        sa.Column("cleared_debit_total", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("cleared_credit_total", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("cleared_debit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cleared_credit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outstanding_debit_total", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("outstanding_credit_total", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("difference", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        _uuid("created_by", nullable=True),
        _uuid("finalized_by", nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        _uuid("report_storage_key", nullable=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=True),
        _uuid("entity_id", nullable=True),
        *_TS,
        sa.UniqueConstraint("bank_account_id", "period_start", "period_end",
                            name="uq_bank_recon_account_period"),
        sa.CheckConstraint("status in ('open','finalized')", name="ck_bank_recon_status"),
    )
    op.create_index("ix_bank_recon_bank_account_id", "bank_reconciliations", ["bank_account_id"])
    op.create_index("ix_bank_recon_status", "bank_reconciliations", ["status"])

    # ── match groups ───────────────────────────────────────────────────────────
    op.create_table(
        "bank_recon_matches",
        _uuid("id", primary_key=True),
        _uuid("reconciliation_id",
              sa.ForeignKey("bank_reconciliations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("method", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        _uuid("advice_id",
              sa.ForeignKey("bank_payment_advices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        _uuid("matched_by", nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        *_TS,
    )
    op.create_index("ix_bank_recon_matches_recon_id", "bank_recon_matches", ["reconciliation_id"])
    op.create_index("ix_bank_recon_matches_advice_id", "bank_recon_matches", ["advice_id"])

    op.create_table(
        "bank_recon_match_txns",
        _uuid("id", primary_key=True),
        _uuid("match_id",
              sa.ForeignKey("bank_recon_matches.id", ondelete="CASCADE"), nullable=False),
        _uuid("bank_transaction_id",
              sa.ForeignKey("bank_transactions.id", ondelete="CASCADE"), nullable=False),
        *_TS,
        # A statement line clears exactly once. This is also what stops the
        # matching ladder from clearing the same line twice on two rungs.
        sa.UniqueConstraint("bank_transaction_id", name="uq_bank_recon_match_txn_once"),
    )
    op.create_index("ix_bank_recon_match_txns_match_id", "bank_recon_match_txns", ["match_id"])

    op.create_table(
        "bank_recon_match_books",
        _uuid("id", primary_key=True),
        _uuid("match_id",
              sa.ForeignKey("bank_recon_matches.id", ondelete="CASCADE"), nullable=False),
        _uuid("jv_line_id",
              sa.ForeignKey("journal_voucher_lines.id", ondelete="SET NULL"), nullable=True),
        sa.Column("nc_voucher_pk", sa.String(40), nullable=True),
        sa.Column("line_no", sa.Integer(), nullable=True),
        sa.Column("account_code", sa.String(40), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        *_TS,
    )
    op.create_index("ix_bank_recon_match_books_match_id", "bank_recon_match_books", ["match_id"])
    op.create_index("ix_bank_recon_match_books_jv_line_id", "bank_recon_match_books", ["jv_line_id"])
    # The healer's lookup key after a full nc_sync nulls jv_line_id.
    op.create_index("ix_bank_recon_match_books_natural", "bank_recon_match_books",
                    ["nc_voucher_pk", "line_no"])

    # ── bank_transactions: which statement a line came from, and its place on it
    op.add_column("bank_transactions",
                  _uuid("statement_id",
                        sa.ForeignKey("bank_statements.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_bank_transactions_statement_id", "bank_transactions", ["statement_id"])
    # The running balance as printed — the verifier's per-line anchor, and what
    # lets the UI show the statement the way the bank did.
    op.add_column("bank_transactions",
                  sa.Column("running_balance", sa.Numeric(15, 2), nullable=True))
    # Statement order. Several lines share one date (RBC prints one balance per
    # date group), so txn_date alone cannot reproduce the page.
    op.add_column("bank_transactions", sa.Column("sort_seq", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("bank_transactions", "sort_seq")
    op.drop_column("bank_transactions", "running_balance")
    op.drop_index("ix_bank_transactions_statement_id", table_name="bank_transactions")
    op.drop_column("bank_transactions", "statement_id")
    op.drop_table("bank_recon_match_books")
    op.drop_table("bank_recon_match_txns")
    op.drop_table("bank_recon_matches")
    op.drop_table("bank_reconciliations")
    op.drop_table("bank_payment_advice_lines")
    op.drop_table("bank_payment_advices")
    op.drop_table("bank_statements")
