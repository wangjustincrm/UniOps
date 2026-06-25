"""bank_accounts.import_mapping JSONB — reusable per-account CSV column map (A3 workbench)

Revision ID: 0012_bank_import_mapping
Revises: 0011_payment_batches
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012_bank_import_mapping"
down_revision = "0011_payment_batches"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bank_accounts", sa.Column("import_mapping", JSONB, nullable=True))


def downgrade():
    op.drop_column("bank_accounts", "import_mapping")
