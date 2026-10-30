"""gr01: over-receipt reason on goods receipts

Revision ID: gr01_over_receipt
Revises: pf02_fee_link_note
Create Date: 2026-10-30

Receiving more than a PO line still has open (ordered less received and
pending) is allowed, but only with a reason (ISS-0098/0169). Nullable;
existing rows stay empty.
"""
import sqlalchemy as sa
from alembic import op

revision = "gr01_over_receipt"
down_revision = "pf02_fee_link_note"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("goods_receipts", sa.Column("over_receipt_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("goods_receipts", "over_receipt_reason")
