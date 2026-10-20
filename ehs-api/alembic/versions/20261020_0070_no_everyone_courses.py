"""No course is required of everyone any more; positions decide.

Round 8 (open box): "on the training courses, remove the everyone must take it
checkmark." The box was the only way to set or clear applies_to_all, so with it
gone a course seeded as "everyone" (WHMIS, Lockout Tagout, Safety Awareness,
Working at Heights) could never be cleared again. Cleared here once, so who
has to take a course is answered in one place: Settings → Positions. On the
test server HSE had already unticked all four.

Revision ID: 20261020_0070
Revises: 20261020_0069
"""
from alembic import op

revision = "20261020_0070"
down_revision = "20261020_0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE ehs_courses SET applies_to_all = false WHERE applies_to_all")


def downgrade() -> None:
    # Which courses were "everyone" is not kept; nothing to restore.
    pass
