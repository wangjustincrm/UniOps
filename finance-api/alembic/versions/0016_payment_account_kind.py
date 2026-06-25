"""bank_accounts.kind (bank | credit_card) + seed COA 2050 Credit Card Payable

A credit card is a funding account whose ledger_account_code points at a
LIABILITY (Credit Card Payable), not a cash asset — paying a vendor by card
credits the liability; paying the card statement later credits the bank.
Unified with bank_accounts so the payment-source picker, statement import and
reconciliation are shared.

Revision ID: 0016_payment_account_kind
Revises: 0015_payment_bank_account
Create Date: 2026-06-23
"""
import uuid

from alembic import op
import sqlalchemy as sa

revision = "0016_payment_account_kind"
down_revision = "0015_payment_bank_account"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bank_accounts",
                  sa.Column("kind", sa.String(20), nullable=False, server_default="bank"))
    # seed a postable liability account cards can map to (idempotent on code)
    op.execute(
        "INSERT INTO chart_of_accounts (id, code, name, account_type, normal_balance, subtype) "
        f"VALUES ('{uuid.uuid4()}', '2050', 'Credit Card Payable', 'liability', 'credit', 'credit_card') "
        "ON CONFLICT (code) DO NOTHING"
    )


def downgrade():
    op.drop_column("bank_accounts", "kind")
    op.execute("DELETE FROM chart_of_accounts WHERE code = '2050'")
