"""A record of each handover of a leaver's open work (round 10, q10-clear).

Revision ID: 20261028_0083
Revises: 20261028_0082
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261028_0083"
down_revision = "20261028_0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_reassignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("leaver_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("leaver_name", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("done_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("done_by_name", sa.String(255), nullable=True),
        sa.Column("summary", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ehs_reassignments_leaver_id", "ehs_reassignments", ["leaver_id"])


def downgrade() -> None:
    n = op.get_bind().exec_driver_sql("SELECT count(*) FROM ehs_reassignments").scalar()
    if n:
        raise RuntimeError(f"ehs_reassignments records {n} handover(s); "
                           "downgrading past 0083 would lose them.")
    op.drop_index("ix_ehs_reassignments_leaver_id", table_name="ehs_reassignments")
    op.drop_table("ehs_reassignments")
