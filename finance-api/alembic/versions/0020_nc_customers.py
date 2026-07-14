"""nc_customers — NC bd_customer direct import (partner dimension)

Revision ID: 0020_nc_customers
Revises: 0019_multi_dim_expand
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0020_nc_customers"
down_revision = "0019_multi_dim_expand"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nc_customers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("code", name="uq_nc_customers_code"),
    )
    op.create_index("ix_nc_customers_code", "nc_customers", ["code"])


def downgrade():
    op.drop_index("ix_nc_customers_code", table_name="nc_customers")
    op.drop_table("nc_customers")
