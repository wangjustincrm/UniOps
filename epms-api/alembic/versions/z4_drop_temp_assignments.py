"""drop dead temp_assignments table (deferred from phase 2+3 authz release)

Revision ID: z4_drop_temp_assignments
Revises: z3_add_created_by_to_tasks
Create Date: 2026-07-16 00:00:00.000000

The temp_assignments table backed the "delegate/acting" (代班) feature that
was removed in the permission-restructure phase 2+3 release. The table was
intentionally kept at that time because, during the migrate -> up -d
deploy window, the old container was still serving traffic and still read
this table (EPMS /config would 500 otherwise). Production is now running
the phase 2+3 code (which no longer references this table), so it is safe
to drop it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'z4_drop_temp_assignments'
down_revision: Union[str, None] = 'z3_add_created_by_to_tasks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("temp_assignments")


def downgrade() -> None:
    # Recreate temp_assignments exactly as it was created in
    # d4e5f6a7b8c9_sprint4_company_config.py, for rollback safety.
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
