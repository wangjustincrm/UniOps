"""budget_actual_cc_map — account-aware NC->UniOps cost-center map

Revision ID: 0025_budget_actual_cc_map
Revises: 0024_jv_source_subsystem
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0025_budget_actual_cc_map"
down_revision = "0024_jv_source_subsystem"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "budget_actual_cc_map",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("account_code", sa.String(10), nullable=False),
        sa.Column("dept_code", sa.String(20), nullable=False),
        sa.Column("nc_cc_code", sa.String(20), nullable=False),
        sa.Column("uniops_cc_code", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_code", "dept_code", "nc_cc_code", name="uq_ba_cc_map"),
    )


def downgrade():
    op.drop_table("budget_actual_cc_map")
