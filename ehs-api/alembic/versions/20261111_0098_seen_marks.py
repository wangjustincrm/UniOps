"""Per-person "seen" marks and baselines for the new-item flags.

Round 12, ACT-01/02/03/04 and INC-08. Nothing is backfilled: a person's
baseline is written the first time they are counted, so nothing that existed
before then reads as new.

Revision ID: 20261111_0098
Revises: 20261110_0097
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261111_0098"
down_revision = "20261110_0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_seen",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("item_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "ehs_seen_baselines",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("news_seen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("ehs_seen_baselines")
    op.drop_table("ehs_seen")
