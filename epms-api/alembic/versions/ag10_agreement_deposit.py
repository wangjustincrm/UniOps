"""ag10: OneTime (Deposit) agreement type

Revision ID: ag10_agreement_deposit
Revises: fr04_freight_project_shipments
Create Date: 2026-11-17

A new agreement type, ``deposit``: a one-time deposit paid without any invoice
(the agreement's attachments are the evidence) and refunded — in full or in
part — when the agreement ends.

* purchase_agreements.deposit_amount — the deposit; the deposit PA's amount is
  locked to it. NULL on every other agreement type.
* payment_applications.deposit_refund_amount / deposit_retained_reason — on the
  settlement PA that records the refund: what the vendor actually returned
  (0 .. deposit) and, when less than the deposit, why the rest was retained.

agreement_type is a plain String(20) with no CHECK constraint, so the new
value needs no DDL. All columns are nullable and additive: finance-api /
expense-api / approval-api map payment_applications with their own models and
ignore extra columns, so the order of migration vs. container start does not
matter here (unlike ag02).
"""
import sqlalchemy as sa
from alembic import op

revision = "ag10_agreement_deposit"
down_revision = "fr04_freight_project_shipments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_agreements",
                  sa.Column("deposit_amount", sa.Numeric(15, 2), nullable=True))
    op.add_column("payment_applications",
                  sa.Column("deposit_refund_amount", sa.Numeric(15, 2), nullable=True))
    op.add_column("payment_applications",
                  sa.Column("deposit_retained_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_applications", "deposit_retained_reason")
    op.drop_column("payment_applications", "deposit_refund_amount")
    op.drop_column("purchase_agreements", "deposit_amount")
