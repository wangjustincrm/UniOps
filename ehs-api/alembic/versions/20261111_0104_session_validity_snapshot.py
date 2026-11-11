"""A course session keeps the course's period as it was when it was scheduled.

Round 12, Q14: "a new valid for period should only affect new course sessions."
Every session already on file takes its course's current period (NULL: does not
expire), which is what it would have used until now.

Revision ID: 20261111_0104
Revises: 20261111_0103
"""
import sqlalchemy as sa
from alembic import op

revision = "20261111_0104"
down_revision = "20261111_0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_course_deliveries",
                  sa.Column("validity_months", sa.Integer(), nullable=True))
    op.execute("UPDATE ehs_course_deliveries d SET validity_months = c.validity_months "
               "FROM ehs_courses c WHERE c.id = d.course_id")


def downgrade() -> None:
    op.drop_column("ehs_course_deliveries", "validity_months")
