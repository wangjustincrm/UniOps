"""qbo mirror phase 2 — AR, more transactions, long-tail raw, attachments

Revision ID: 0028_qbo_mirror_phase2
Revises: 0027_qbo_mirror_ap_core
Create Date: 2026-07-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0028_qbo_mirror_phase2"
down_revision = "0027_qbo_mirror_ap_core"
branch_labels = None
depends_on = None

# Phase-1 line tables that gain posting_type.
_EXISTING_LINE_TABLES = ("qbo_bill_lines", "qbo_bill_payment_lines", "qbo_vendor_credit_lines")


def upgrade() -> None:
    for t in _EXISTING_LINE_TABLES:
        op.add_column(t, sa.Column("posting_type", sa.String(10)))
    # New tables are added by later steps of this migration (later Phase-2 tasks).


def downgrade() -> None:
    for t in _EXISTING_LINE_TABLES:
        op.drop_column(t, "posting_type")
