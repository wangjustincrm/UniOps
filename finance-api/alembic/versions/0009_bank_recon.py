"""bank_accounts + bank_transactions + exchange_rates (Phase a A3)

Revision ID: 0009_bank_recon
Revises: 0008_aux_required
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0009_bank_recon"
down_revision = "0008_aux_required"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bank_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("bank_name", sa.String(255), nullable=False),
        sa.Column("account_masked", sa.String(40), nullable=True),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("ledger_account_code", sa.String(10), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "bank_transactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("bank_account_id", UUID(as_uuid=True),
                  sa.ForeignKey("bank_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("txn_date", sa.Date, nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("status", sa.String(12), nullable=False, server_default="unmatched"),
        sa.Column("matched_payment_id", UUID(as_uuid=True),
                  sa.ForeignKey("payment_records.id", ondelete="SET NULL"), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("matched_by", UUID(as_uuid=True), nullable=True),
        sa.Column("import_hash", sa.String(64), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("import_hash", name="uq_bank_transactions_import_hash"),
    )
    op.create_index("ix_bank_transactions_bank_account_id", "bank_transactions", ["bank_account_id"])
    op.create_index("ix_bank_transactions_txn_date", "bank_transactions", ["txn_date"])
    op.create_index("ix_bank_transactions_status", "bank_transactions", ["status"])

    op.create_table(
        "exchange_rates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("from_currency", sa.String(10), nullable=False),
        sa.Column("to_currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("from_currency", "to_currency", "effective_date",
                            name="uq_exchange_rates_pair_date"),
    )
    op.create_index("ix_exchange_rates_effective_date", "exchange_rates", ["effective_date"])


def downgrade():
    op.drop_table("exchange_rates")
    op.drop_table("bank_transactions")
    op.drop_table("bank_accounts")
