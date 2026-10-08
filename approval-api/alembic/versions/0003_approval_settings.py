"""approval_settings: engine-wide switches that are not per-department.

Revision ID: 0003_approval_settings
Revises: 0002_approval_delegations
Create Date: 2026-09-06

`approval_dept_routing` answers "who approves THIS department's gm_or_opm
step". A payment application that covers purchase orders from several
departments has no such answer — one department may route to GM and another to
OPM, and the engine cannot pick between them. This table holds the settings
that apply to the engine as a whole rather than to one department; the first
of them is which post approves those cross-department payments.

Seeded with 'gm' so behaviour is defined from the moment the column exists
rather than depending on an admin visiting the settings screen first.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_approval_settings"
down_revision = "0002_approval_delegations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_settings",
        sa.Column("key", sa.String(50), primary_key=True),
        sa.Column("value", sa.String(50), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        "INSERT INTO approval_settings (key, value) "
        "VALUES ('cross_department_gm_or_opm', 'gm')"
    )


def downgrade() -> None:
    op.drop_table("approval_settings")
