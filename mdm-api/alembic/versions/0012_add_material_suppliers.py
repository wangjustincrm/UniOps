"""material_suppliers — supply parameters, hand-maintained (MRP phase0 task 6)

Revision ID: 0012_add_material_suppliers
Revises: 0011_add_boms
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0012_add_material_suppliers"
down_revision = "0011_add_boms"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "material_suppliers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("material_code", sa.String(50), nullable=False),
        sa.Column("partner_code", sa.String(50), nullable=False),
        sa.Column("lead_time_days", sa.Integer()),
        sa.Column("moq", sa.Numeric(18, 4)),
        sa.Column("order_multiple", sa.Numeric(18, 4)),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("price_ref", sa.Numeric(18, 4)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("material_code", "partner_code", name="uq_material_suppliers_material_partner"),
    )
    op.create_index("ix_material_suppliers_material_code", "material_suppliers", ["material_code"])


def downgrade():
    op.drop_table("material_suppliers")
