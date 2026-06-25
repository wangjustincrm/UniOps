"""create departments and cost_centers tables

Revision ID: 481b6647d22d
Revises: 0f934f951dac
Create Date: 2026-03-24 14:44:08.114839

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '481b6647d22d'
down_revision: Union[str, None] = '0f934f951dac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # departments
    op.create_table(
        "departments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_departments_code", "departments", ["code"], unique=True)

    # cost_centers
    op.create_table(
        "cost_centers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "department_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_cost_centers_code", "cost_centers", ["code"], unique=True)
    op.create_index("ix_cost_centers_department_id", "cost_centers", ["department_id"])

    # add FK from users.department_id → departments.id
    op.create_foreign_key(
        "fk_users_department_id",
        "users", "departments",
        ["department_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_department_id", "users", type_="foreignkey")
    op.drop_index("ix_cost_centers_department_id", table_name="cost_centers")
    op.drop_index("ix_cost_centers_code", table_name="cost_centers")
    op.drop_table("cost_centers")
    op.drop_index("ix_departments_code", table_name="departments")
    op.drop_table("departments")
