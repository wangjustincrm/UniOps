"""uom_conversions master, mirrored from NC ERP unitTranf (MRP phase0 task 3)

Revision ID: 0009_add_uom_conversions
Revises: 0008_erp_material_exp_family
Create Date: 2026-08-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0009_add_uom_conversions"
down_revision = "0008_erp_material_exp_family"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "uom_conversions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("from_uom", sa.String(20), nullable=False),
        sa.Column("to_uom", sa.String(20), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("from_uom", "to_uom", name="uq_uom_conv"),
    )
    op.create_index("ix_uom_conversions_from_uom", "uom_conversions", ["from_uom"], unique=False)
    op.create_index("ix_uom_conversions_to_uom", "uom_conversions", ["to_uom"], unique=False)


def downgrade():
    op.drop_index("ix_uom_conversions_to_uom", table_name="uom_conversions")
    op.drop_index("ix_uom_conversions_from_uom", table_name="uom_conversions")
    op.drop_table("uom_conversions")
