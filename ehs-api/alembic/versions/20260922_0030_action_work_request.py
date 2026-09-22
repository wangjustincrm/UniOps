"""A corrective action carries the maintenance work request it was raised as.

HSE asked for a box titled "work request number" on the action. It is not a
note: most physical corrective actions — guard the pinch point, replace the
eyewash, clear the exit — are done by maintenance under their own work
request, in their own system. Without the number the two records cannot be
tied together, so "has it been done" has to be answered by asking somebody,
and the CAPA is closed on that answer.

Free text on purpose. The maintenance system is not ours and its numbering is
not ours to validate; a format rule here would reject a real number the day
they change theirs.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260922_0030" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260922_0030"
down_revision = "20260922_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_actions",
                  sa.Column("work_request_no", sa.String(60), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_actions", "work_request_no")
