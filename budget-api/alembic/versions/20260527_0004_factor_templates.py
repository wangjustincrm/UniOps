"""Add factor_templates + factor_template_values for the reusable Factor Library.

Templates are an optional starting point for per-Account decomposition factors:
selecting a template copies its factor_code/name + values into the existing
`budget_account_factors` / `budget_account_factor_values` tables. Once attached
the per-Account rows are independent — there is no FK from per-Account factors
back to templates.

Revision ID: 20260527_0004
Revises: 20260519_0003
Create Date: 2026-05-27
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260527_0004"
down_revision = "20260519_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "factor_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("factor_code", sa.String(length=30), nullable=False),
        sa.Column("factor_name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_factor_templates_factor_code", "factor_templates", ["factor_code"], unique=True)

    op.create_table(
        "factor_template_values",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value_code", sa.String(length=50), nullable=False),
        sa.Column("value_name", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["template_id"], ["factor_templates.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("template_id", "value_code", name="uq_factor_template_value_code"),
    )
    op.create_index("ix_factor_template_values_template_id", "factor_template_values", ["template_id"])


def downgrade() -> None:
    op.drop_index("ix_factor_template_values_template_id", table_name="factor_template_values")
    op.drop_table("factor_template_values")
    op.drop_index("ix_factor_templates_factor_code", table_name="factor_templates")
    op.drop_table("factor_templates")
