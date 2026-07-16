"""drop temp_assignments — dead feature (approval engine never read it)

Revision ID: z4_drop_temp_assignments
Revises: z3_add_created_by_to_tasks
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'z4_drop_temp_assignments'
down_revision: Union[str, None] = 'z3_add_created_by_to_tasks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("temp_assignments")


def downgrade() -> None:
    op.create_table(
        "temp_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("delegate_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("role_key", sa.String(50), nullable=False, index=True),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
