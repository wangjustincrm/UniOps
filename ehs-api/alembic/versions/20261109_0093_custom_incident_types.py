"""Incident types HSE add in Settings.

Round 12, GEN-01 and Q10: "HSE can add new types and set the label, form,
category and who can pick them, but not the legal flags." The built-in types
stay in code.

Revision ID: 20261109_0093
Revises: 20261109_0092
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261109_0093"
down_revision = "20261109_0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_incident_types",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("form_kind", sa.String(20), nullable=False),
        sa.Column("category_id", UUID(as_uuid=True), nullable=True),
        sa.Column("category_label", sa.String(200), nullable=True),
        sa.Column("pick_permission", sa.String(60), nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ehs_incident_types")
