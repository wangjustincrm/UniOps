"""Add updated_at to budget_account_factor_values.

The SQLAlchemy model has had TimestampMixin (created_at + updated_at) since
v1, but the initial migration accidentally omitted `updated_at` on this one
table. Inserts blow up with `column ... does not exist` when SQLAlchemy
refreshes the row after INSERT (it tries to SELECT all model columns).

Revision ID: 20260519_0003
Revises: 20260518_0002
Create Date: 2026-05-19
"""
import sqlalchemy as sa
from alembic import op


revision = "20260519_0003"
down_revision = "20260518_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='budget_account_factor_values' "
        "AND column_name='updated_at'"
    )).first()
    if exists:
        return
    op.add_column(
        "budget_account_factor_values",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='budget_account_factor_values' "
        "AND column_name='updated_at'"
    )).first()
    if exists:
        op.drop_column("budget_account_factor_values", "updated_at")
