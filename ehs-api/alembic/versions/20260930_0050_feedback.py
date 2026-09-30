"""Somewhere to record a general comment

Round 4: "add a general comments feedback place to allow for general comments
for other issues found." Every other record here is about a specific obligation,
and somebody who notices a screen is confusing or a form asks the wrong question
had nowhere to put it — so it arrived in the answer box of an acceptance script,
or not at all.

Deliberately not a task and not an approval: nobody is assigned a comment.

Revision ID: 20260930_0050
Revises: 20260929_0049
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260930_0050"
down_revision = "20260929_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("page_path", sa.String(length=200), nullable=True),
        # ★ SET NULL with a name snapshot beside it, as every users reference in
        # this module does: a comment from somebody who has left still has to
        # say who made it.
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("submitted_by_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="new"),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("answered_by_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_ehs_feedback_submitted_by", "ehs_feedback", ["submitted_by"])
    # The list HSE opens is "what has not been looked at yet", newest first.
    op.create_index("ix_ehs_feedback_status_created",
                    "ehs_feedback", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ehs_feedback_status_created", table_name="ehs_feedback")
    op.drop_index("ix_ehs_feedback_submitted_by", table_name="ehs_feedback")
    op.drop_table("ehs_feedback")
