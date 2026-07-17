"""journal_vouchers.source_subsystem — NC GL_VOUCHER.PK_SYSTEM (GL/AP/AR/...)

Revision ID: 0024_jv_source_subsystem
Revises: 0023_coa_sync_runs
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_jv_source_subsystem"
down_revision = "0023_coa_sync_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("journal_vouchers",
                  sa.Column("source_subsystem", sa.String(10), nullable=True))


def downgrade():
    op.drop_column("journal_vouchers", "source_subsystem")
