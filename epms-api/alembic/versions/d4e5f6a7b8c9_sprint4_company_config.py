"""sprint4: company_config, temp_assignments

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-03-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── company_config (singleton) ──────────────────────────────────────────
    op.create_table(
        "company_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default="EPMS"),
        sa.Column("tagline", sa.String(500), nullable=False,
                  server_default="Enterprise Procurement Management"),
        sa.Column("logo_data_url", sa.Text, nullable=True),
        sa.Column("logo_file_name", sa.String(255), nullable=True),
        sa.Column("delivery_address", sa.Text, nullable=False, server_default=""),
        sa.Column("default_currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("enabled_currencies", postgresql.JSONB, nullable=False,
                  server_default='["CAD","USD","EUR","RMB"]'),
        sa.Column("custom_currencies", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("mfa_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("password_expiry_days", sa.Integer, nullable=True),
        sa.Column("po_email_subject", sa.String(500), nullable=False,
                  server_default="Purchase Order {po_number} from {company_name}"),
        sa.Column("po_email_body", sa.Text, nullable=False, server_default=""),
        sa.Column("pdf_templates", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("workflow_config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("dept_gm_opm_mapping", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("dept_supervisor_enabled", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("service_gr_sla", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("gr_notification_sla", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("prepayment_config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("budget_admin_config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("collection_config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("role_management", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("workflow_defs", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )

    # ── temp_assignments ────────────────────────────────────────────────────
    op.create_table(
        "temp_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("delegate_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_key", sa.String(50), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_temp_assignments_delegate_user_id", "temp_assignments", ["delegate_user_id"])
    op.create_index("ix_temp_assignments_role_key", "temp_assignments", ["role_key"])


def downgrade() -> None:
    op.drop_table("temp_assignments")
    op.drop_table("company_config")
