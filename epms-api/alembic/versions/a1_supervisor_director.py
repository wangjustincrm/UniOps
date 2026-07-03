"""supervisor_id on users + dept_director_mapping on company_config

Revision ID: a1_supervisor_director
Revises: z2_add_password_changed_at
Create Date: 2026-07-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a1_supervisor_director"
down_revision: Union[str, None] = "z2_add_password_changed_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("supervisor_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_supervisor_id_users",
        "users", "users",
        ["supervisor_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_users_supervisor_id", "users", ["supervisor_id"])
    op.add_column(
        "company_config",
        sa.Column("dept_director_mapping", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("company_config", "dept_director_mapping")
    op.drop_index("ix_users_supervisor_id", table_name="users")
    op.drop_constraint("fk_users_supervisor_id_users", "users", type_="foreignkey")
    op.drop_column("users", "supervisor_id")
