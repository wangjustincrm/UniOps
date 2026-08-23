"""materials master table, promoted from the erp_material mirror

Formal material master (MRP phase0 task 2). code (== erp_material.erp_part_no)
is the global material key later MRP-phase0 tasks join on.

Revision ID: 0007_create_materials
Revises: 0006_partner_remittance_email
Create Date: 2026-08-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0007_create_materials"
down_revision = "0006_partner_remittance_email"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "materials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("spec", sa.String(255), nullable=True),
        sa.Column("item_type", sa.String(20), nullable=True),
        sa.Column("erp_item_type", sa.String(20), nullable=True),
        sa.Column("base_uom", sa.String(20), nullable=True),
        sa.Column("shelf_life_months", sa.Integer, nullable=True),
        sa.Column("procurement_type", sa.String(20), nullable=False, server_default="purchase"),
        sa.Column("product_family", sa.String(100), nullable=True),
        sa.Column("factory_code", sa.String(50), nullable=True),
        sa.Column("erp_id", sa.String(50), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_materials_code"),
    )
    op.create_index("ix_materials_code", "materials", ["code"], unique=True)
    op.create_index("ix_materials_erp_id", "materials", ["erp_id"], unique=False)


def downgrade():
    op.drop_index("ix_materials_erp_id", table_name="materials")
    op.drop_index("ix_materials_code", table_name="materials")
    op.drop_table("materials")
