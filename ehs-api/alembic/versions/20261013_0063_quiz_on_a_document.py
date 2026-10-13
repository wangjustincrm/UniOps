"""A quiz can test a document, as well as a course.

Round 6, answer box 1: "add a quiz to a document". A quiz belongs to a course
(the sessions use it) or to a document (signing it asks for a pass first) — or
to neither, a teaching aid on its own — never to both.

Revision ID: 20261013_0063
Revises: 20261013_0062
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261013_0063"
down_revision = "20261013_0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_quizzes", sa.Column(
        "document_id", UUID(as_uuid=True),
        sa.ForeignKey("ehs_documents.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_ehs_quizzes_document_id", "ehs_quizzes", ["document_id"])
    op.create_check_constraint("ck_ehs_quiz_one_subject", "ehs_quizzes",
                               "num_nonnulls(course_id, document_id) <= 1")


def downgrade() -> None:
    op.drop_constraint("ck_ehs_quiz_one_subject", "ehs_quizzes", type_="check")
    op.drop_index("ix_ehs_quizzes_document_id", table_name="ehs_quizzes")
    op.drop_column("ehs_quizzes", "document_id")
