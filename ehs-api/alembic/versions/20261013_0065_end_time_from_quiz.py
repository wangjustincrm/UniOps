"""Say when a session's end time came from the first quiz pass.

Round 6 review (uniops-93). The end inferred from the first pass is Kirk's way
of recording how long the training was; it must not also close the door. A
pass three minutes in — an easy quiz, somebody who sat the course before, a
code photographed outside the room — shut the join window thirty minutes later
on everyone actually there. So the inferred end is marked, the join window
counts only an end somebody entered, and the page says where the time came
from.

Revision ID: 20261013_0065
Revises: 20261013_0064
"""
import sqlalchemy as sa
from alembic import op

revision = "20261013_0065"
down_revision = "20261013_0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_course_deliveries", sa.Column(
        "end_time_from_quiz", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("ehs_course_deliveries", "end_time_from_quiz")
