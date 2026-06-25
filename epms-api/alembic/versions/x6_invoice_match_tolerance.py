"""company_config.invoice_match_tolerance_pct — 3-way match tolerance (A2)

Revision ID: x6_match_tolerance
Revises: x5_repoint_vendor_fks
Create Date: 2026-06-12
"""
from alembic import op
import sqlalchemy as sa

revision = "x6_match_tolerance"
down_revision = "x5_repoint_vendor_fks"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("company_config",
                  sa.Column("invoice_match_tolerance_pct", sa.Numeric(5, 2),
                            nullable=False, server_default="0"))


def downgrade():
    op.drop_column("company_config", "invoice_match_tolerance_pct")
