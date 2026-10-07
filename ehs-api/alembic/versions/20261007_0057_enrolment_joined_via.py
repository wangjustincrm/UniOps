"""How somebody got on a session roster: added to it, or scanned in.

Round 5, answer box 4: scanning the quiz code at the end of a course puts the
person on that session's roster. The person running the session needs to see
who joined that way before closing it, so a code passed round outside the room
can be caught. Every row already there was added by hand, which is the default.

Revision ID: 20261007_0057
Revises: 20261007_0056
"""
import sqlalchemy as sa
from alembic import op

revision = "20261007_0057"
down_revision = "20261007_0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_course_enrolments", sa.Column(
        "joined_via", sa.String(10), nullable=False, server_default="roster"))


def downgrade() -> None:
    op.drop_column("ehs_course_enrolments", "joined_via")
