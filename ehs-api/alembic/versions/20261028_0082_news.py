"""HSE news on My Actions (round 10, open box).

Revision ID: 20261028_0082
Revises: 20261028_0081
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261028_0082"
down_revision = "20261028_0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_news",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("importance", sa.String(6), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("posted_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("posted_by_name", sa.String(255), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("importance IN ('high', 'medium', 'low')", name="ck_ehs_news_importance"),
    )
    op.create_index("ix_ehs_news_posted_at", "ehs_news", ["posted_at"])


def downgrade() -> None:
    n = op.get_bind().exec_driver_sql("SELECT count(*) FROM ehs_news").scalar()
    if n:
        raise RuntimeError(f"ehs_news holds {n} item(s) HSE posted; downgrading past 0082 would lose them.")
    op.drop_index("ix_ehs_news_posted_at", table_name="ehs_news")
    op.drop_table("ehs_news")
