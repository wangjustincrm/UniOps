"""create budget_l1 and budget_accounts tables

Revision ID: d35208b1b063
Revises: 481b6647d22d
Create Date: 2026-03-24 14:49:06.501644

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd35208b1b063'
down_revision: Union[str, None] = '481b6647d22d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # budget_l1
    op.create_table(
        "budget_l1",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "cost_center_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cost_centers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_budget_l1_code", "budget_l1", ["code"], unique=True)
    op.create_index("ix_budget_l1_cost_center_id", "budget_l1", ["cost_center_id"])

    # budget_accounts
    op.create_table(
        "budget_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "l1_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("budget_l1.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("annual_budget", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("committed", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("actual_spent", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_budget_accounts_code", "budget_accounts", ["code"], unique=True)
    op.create_index("ix_budget_accounts_l1_id", "budget_accounts", ["l1_id"])


def downgrade() -> None:
    op.drop_index("ix_budget_accounts_l1_id", table_name="budget_accounts")
    op.drop_index("ix_budget_accounts_code", table_name="budget_accounts")
    op.drop_table("budget_accounts")
    op.drop_index("ix_budget_l1_cost_center_id", table_name="budget_l1")
    op.drop_index("ix_budget_l1_code", table_name="budget_l1")
    op.drop_table("budget_l1")
