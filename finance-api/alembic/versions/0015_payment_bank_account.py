"""payment_records.bank_account_id + payment_batches.bank_account_id

Records which bank account funded a payment / payment run. Chosen at execution
time in the Payment Batches UI. Bare UUID (no cross-table FK — bank_accounts is
finance-owned but kept loosely coupled like the other doc references).

Revision ID: 0015_payment_bank_account
Revises: 0014_ap_invoices
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0015_payment_bank_account"
down_revision = "0014_ap_invoices"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payment_records", sa.Column("bank_account_id", UUID(as_uuid=True), nullable=True))
    op.add_column("payment_batches", sa.Column("bank_account_id", UUID(as_uuid=True), nullable=True))


def downgrade():
    op.drop_column("payment_batches", "bank_account_id")
    op.drop_column("payment_records", "bank_account_id")
