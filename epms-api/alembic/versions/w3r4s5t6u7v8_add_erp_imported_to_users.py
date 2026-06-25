"""add erp_imported provenance flag to users

Distinguishes users imported from ERP (code is read-only) from manually
created users (ERP Code is editable). Backfills existing rows: any user that
already has an erp_person_code today was created by the ERP import flow.

Revision ID: w3r4s5t6u7v8
Revises: v2q3r4s5t6u7
Create Date: 2026-06-08
"""
from alembic import op
import sqlalchemy as sa


revision = "w3r4s5t6u7v8"
down_revision = "v2q3r4s5t6u7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("erp_imported", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Backfill: the manual-ERP-code feature is brand new, so every existing user
    # that has an erp_person_code was imported from ERP.
    op.execute("UPDATE users SET erp_imported = true WHERE erp_person_code IS NOT NULL")


def downgrade():
    op.drop_column("users", "erp_imported")
