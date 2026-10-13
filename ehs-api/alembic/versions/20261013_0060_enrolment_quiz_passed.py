"""A roster row says whether the person passed the session's quiz.

Round 6, answer box 2: "in addition to the passed, failed, did not attend
buttons on the course session, add a button that indicates that the quiz has
been passed by the attendee. in order for an attendee to pass the training
session, they must have passed the quiz and the run by person must also mark
them as passed before saving and closing the session and generating a
training certificate."

So passing the quiz no longer passes the session: it sets this, and the person
running it decides. Rows the quiz marked passed under round 6's rule keep their
pass and record, and are given the quiz mark they earned.

Revision ID: 20261013_0060
Revises: 20261013_0059
"""
import sqlalchemy as sa
from alembic import op

revision = "20261013_0060"
down_revision = "20261013_0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_course_enrolments", sa.Column(
        "quiz_passed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ehs_course_enrolments", sa.Column(
        "quiz_marked_by_name", sa.String(255), nullable=True))
    op.execute("""
        UPDATE ehs_course_enrolments
           SET quiz_passed_at = marked_at, quiz_marked_by_name = 'Online quiz'
         WHERE marked_by IS NULL AND marked_by_name = 'Online quiz' AND result = 'passed'
    """)


def downgrade() -> None:
    op.drop_column("ehs_course_enrolments", "quiz_marked_by_name")
    op.drop_column("ehs_course_enrolments", "quiz_passed_at")
