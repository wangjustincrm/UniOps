"""Photographs taken while filling in a form.

Same arrangement as incident attachments: bytes to file-api, the join here,
because file-api has no "list by document" endpoint and the owning service is
what remembers which files belong to which record.

`question_key` is what distinguishes this from the incident case. A checklist
can ask for photographs at several points — one per failed item is the usual
pattern — so an attachment belongs to a question, not just to the submission.
Nullable for anything attached to the form as a whole.

Revision ID: 20260831_0010
Revises: 20260831_0009
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260831_0010"
down_revision = "20260831_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_form_attachments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("submission_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_form_submissions.id", ondelete="CASCADE"), nullable=False),
        # Which question this answers with a picture. Null = attached to the form.
        sa.Column("question_key", sa.String(80), nullable=True),
        sa.Column("storage_key", UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ehs_form_attachments_submission", "ehs_form_attachments",
                    ["submission_id"])


def downgrade() -> None:
    op.drop_table("ehs_form_attachments")
