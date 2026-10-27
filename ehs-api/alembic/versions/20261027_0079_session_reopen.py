"""Reopening a closed training session, with the reason.

Review (uniops-8b) of round 10, TC-05: a closed session is read only, and
its refusal said to correct a result on the person's training record — for
which there is no way. HSE may reopen a session saying why; closing it
again withdraws a record it issued to somebody no longer marked passed.

Revision ID: 20261027_0079
Revises: 20261027_0078
"""
import sqlalchemy as sa
from alembic import op

revision = "20261027_0079"
down_revision = "20261027_0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_course_deliveries", sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ehs_course_deliveries", sa.Column("reopened_by_name", sa.String(255), nullable=True))
    op.add_column("ehs_course_deliveries", sa.Column("reopen_reason", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_course_deliveries", "reopen_reason")
    op.drop_column("ehs_course_deliveries", "reopened_by_name")
    op.drop_column("ehs_course_deliveries", "reopened_at")
