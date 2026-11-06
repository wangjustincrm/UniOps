"""Pay part of a PA: which invoices a batch line / payment covers.

Finance, 2026-11-06: a PA can hold several invoices and a payment run may pay
only some of them. Both columns are NULL for the whole document, which is what
every existing row means.

Revision ID: 0044_payment_invoice_scope
Revises: 0043_fx_rates_nc_push
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0044_payment_invoice_scope"
down_revision = "0043_fx_rates_nc_push"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_batch_lines", sa.Column("invoice_ids", JSONB, nullable=True))
    op.add_column("payment_records", sa.Column("invoice_ids", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("payment_records", "invoice_ids")
    op.drop_column("payment_batch_lines", "invoice_ids")
