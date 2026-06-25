"""Baseline — PA table already exists (managed by epms-api).

expense-api shares the payment_applications table. No schema changes needed
at this revision. Future expense-specific tables (expense_claims, etc.) will
be added in subsequent revisions.

Revision ID: 0001
Revises:
Create Date: 2026-04-28
"""
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # payment_applications table is owned by epms-api migrations


def downgrade() -> None:
    pass
