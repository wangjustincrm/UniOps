"""tax_code snapshot on PO + PA (Finance Tax MDM wiring)

Adds the chosen mdm-api tax_codes.code (plus a rate snapshot on PA) so PO/PA
record WHICH tax code was applied, not just a bare rate. Nullable + no backfill:
legacy rows keep their numeric rate and leave tax_code NULL.

Revision ID: x8_add_tax_code_snapshot
Revises: x7_add_module_taglines
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = "x8_add_tax_code_snapshot"
down_revision = "x7_add_module_taglines"
branch_labels = None
depends_on = None


def upgrade():
    # PO already has tax_rate (the rate snapshot); add the code it came from.
    op.add_column("purchase_orders",
                  sa.Column("tax_code", sa.String(length=20), nullable=True))
    # PA is amount-only today; add both the code and its rate snapshot.
    op.add_column("payment_applications",
                  sa.Column("tax_code", sa.String(length=20), nullable=True))
    op.add_column("payment_applications",
                  sa.Column("tax_rate", sa.Numeric(5, 4), nullable=True))


def downgrade():
    op.drop_column("payment_applications", "tax_rate")
    op.drop_column("payment_applications", "tax_code")
    op.drop_column("purchase_orders", "tax_code")
