"""tasks.created_by — 记录任务发起人(match 指派的 assigner,复核任务路由用)

Revision ID: z3_add_created_by_to_tasks
Revises: a2_workflow_defs_optional_levels
Create Date: 2026-07-09 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'z3_add_created_by_to_tasks'
# 挂在真实 head(a2)之下 — 之前误挂 z2 造成双 head 分叉,
# downgrade 验证时把 a1/a2 分支(users.supervisor_id 等)连带回滚过一次。
down_revision: Union[str, None] = 'a2_workflow_defs_optional_levels'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column(
        'created_by', UUID(as_uuid=True),
        sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True,
    ))


def downgrade() -> None:
    op.drop_column('tasks', 'created_by')
