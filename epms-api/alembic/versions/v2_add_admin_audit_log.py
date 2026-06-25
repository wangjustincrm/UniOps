"""Add admin_audit_log table for the data-maintenance admin.

Revision ID: v2q3r4s5t6u7
Revises: u1p2q3r4s5t6
Create Date: 2026-06-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v2q3r4s5t6u7"
down_revision = "u1p2q3r4s5t6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_email", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("system", sa.String(length=20), nullable=False),
        sa.Column("entity", sa.String(length=40), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_number", sa.String(length=64), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cascade_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_audit_log_actor_id", "admin_audit_log", ["actor_id"])
    op.create_index("ix_admin_audit_log_entity", "admin_audit_log", ["entity"])
    op.create_index("ix_admin_audit_log_record_id", "admin_audit_log", ["record_id"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_log_record_id", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_entity", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_actor_id", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
