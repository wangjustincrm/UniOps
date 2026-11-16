"""approval_workflow_snapshot: the workflows in-flight documents are aligned to.

Revision ID: 0004_workflow_snapshot
Revises: 0003_approval_settings
Create Date: 2026-11-16

Editing an approval workflow must carry in-flight documents over to the new
chain (incident 2026-11-16: the PA (Direct) chain's Finance Manager and Finance
BP steps were swapped and 27 PAs were left "at" the wrong step). That needs the
chain as it was before the edit; approval-api keeps it here, one row (id = 1).

Seeded from company_config.workflow_defs as it stands at upgrade time: every
in-flight document is aligned to the current chains at that moment.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_workflow_snapshot"
down_revision = "0003_approval_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_workflow_snapshot",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("workflow_defs", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        "INSERT INTO approval_workflow_snapshot (id, workflow_defs) "
        "SELECT 1, COALESCE((SELECT workflow_defs FROM company_config LIMIT 1), '{}'::jsonb)"
    )


def downgrade() -> None:
    op.drop_table("approval_workflow_snapshot")
