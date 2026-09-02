"""Training quizzes, answered on a phone by scanning a code.

HSE's picture: the trainer puts a QR code on the screen at the end of a
session, everybody scans it and answers on their own phone, and the score lands
in their training record.

The reason `correct` is a column on the question rather than part of one JSON
blob with the rest is that it must never reach the browser. The schema the
learner receives is a different shape from the row, and scoring happens on the
server. A quiz whose answers are in the page source tests nothing, and it is an
easy thing to build by accident — the obvious implementation serialises the
whole question object.

Every attempt is kept. Somebody who failed twice and passed on the third is a
different training record from somebody who passed first time, and "how do you
know they understood it" is owed the real history rather than the flattering
one.

Revision ID: 20260902_0024
Revises: 20260902_0023
Create Date: 2026-09-02
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260902_0024"
down_revision = "20260902_0023"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "ehs_quizzes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("course_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_courses.id", ondelete="CASCADE"),
                  nullable=True, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("intro", sa.Text, nullable=True),
        sa.Column("pass_mark_percent", sa.Integer, nullable=False, server_default="80"),
        sa.Column("max_attempts", sa.Integer, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("qr_token", sa.String(64), nullable=True, unique=True),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    op.create_table(
        "ehs_quiz_questions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("quiz_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_quizzes.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("seq", sa.Integer, nullable=False, server_default="1"),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("kind", sa.String(12), nullable=False, server_default="single"),
        sa.Column("options", JSONB, nullable=False, server_default="[]"),
        # Server-side only. Stripped by the schema the learner receives.
        sa.Column("correct", JSONB, nullable=False, server_default="[]"),
        sa.Column("explanation", sa.Text, nullable=True),
        sa.Column("points", sa.Integer, nullable=False, server_default="1"),
    )

    op.create_table(
        "ehs_quiz_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("quiz_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_quizzes.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("delivery_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_course_deliveries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), **_TS),
        # Kept so a trainer can see which question the room got wrong, which is
        # the useful output of running one of these.
        sa.Column("answers", JSONB, nullable=False, server_default="{}"),
        sa.Column("score_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("points_scored", sa.Integer, nullable=False, server_default="0"),
        sa.Column("points_possible", sa.Integer, nullable=False, server_default="0"),
        sa.Column("passed", sa.Boolean, nullable=False, server_default="false", index=True),
        sa.Column("training_record_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_training_records.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )


def downgrade() -> None:
    op.drop_table("ehs_quiz_attempts")
    op.drop_table("ehs_quiz_questions")
    op.drop_table("ehs_quizzes")
