"""Add po_smtp_* fields to company_config for vendor-facing PO emails.

Splits SMTP configuration into two profiles:
  - smtp_*       — internal task notifications (approval emails, reminders, MFA OTP)
  - po_smtp_*    — Purchase Order emails sent to external vendors (NEW)

All new fields are NULLable so existing installs fall back to the internal
smtp_* profile (preserves current behavior). UI to configure po_smtp_* lives
in EPMS → Admin Panel → Email Settings.

Revision ID: u1p2q3r4s5t6
Revises: t0o1p2q3r4s5
Create Date: 2026-05-28
"""
import sqlalchemy as sa
from alembic import op


revision = "u1p2q3r4s5t6"
down_revision = "t0o1p2q3r4s5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("company_config", sa.Column("po_smtp_host", sa.String(length=255), nullable=True))
    op.add_column("company_config", sa.Column("po_smtp_port", sa.Integer(), nullable=True))
    op.add_column("company_config", sa.Column("po_smtp_user", sa.String(length=255), nullable=True))
    op.add_column("company_config", sa.Column("po_smtp_password", sa.String(length=255), nullable=True))
    op.add_column("company_config", sa.Column("po_smtp_use_tls", sa.Boolean(), nullable=True))
    op.add_column("company_config", sa.Column("po_smtp_from", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("company_config", "po_smtp_from")
    op.drop_column("company_config", "po_smtp_use_tls")
    op.drop_column("company_config", "po_smtp_password")
    op.drop_column("company_config", "po_smtp_user")
    op.drop_column("company_config", "po_smtp_port")
    op.drop_column("company_config", "po_smtp_host")
