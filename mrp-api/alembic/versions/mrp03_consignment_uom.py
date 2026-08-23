"""add uom to mrp_consignment_stock

Revision ID: mrp03_consignment_uom
Revises: mrp02_forecast_consignment
"""
from alembic import op
import sqlalchemy as sa

revision = "mrp03_consignment_uom"
down_revision = "mrp02_forecast_consignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mrp_consignment_stock",
        sa.Column("uom", sa.String(length=10), nullable=False, server_default="KG"),
    )


def downgrade() -> None:
    op.drop_column("mrp_consignment_stock", "uom")
