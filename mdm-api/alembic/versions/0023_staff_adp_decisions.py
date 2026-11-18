"""Staff: ADP differences wait for Follow ADP / Keep Current

docs/prd/EMPLOYEES.md IMP-016. Additive: nobody has a pending difference after
the upgrade; the next import fills them.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0023_staff_adp_decisions"
down_revision = "0022_staff_hire_pay_manager"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("adp_pending", JSONB(), nullable=True))
    op.add_column("employees", sa.Column("adp_pending_import_id", UUID(as_uuid=True), nullable=True))
    op.add_column("employees", sa.Column("adp_pending_since", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("adp_kept", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    for column in ("adp_kept", "adp_pending_since", "adp_pending_import_id", "adp_pending"):
        op.drop_column("employees", column)
