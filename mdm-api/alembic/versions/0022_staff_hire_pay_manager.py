"""Staff: hire date, pay type and the Manager tick

docs/prd/EMPLOYEES.md §3, IMP-015, EMP-009. Additive; existing employees get
empty hire date and pay type (filled by the next roster that has the columns)
and Manager = false.
"""
import sqlalchemy as sa
from alembic import op

revision = "0022_staff_hire_pay_manager"
down_revision = "0021_org_chart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("hire_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("pay_type", sa.String(10), nullable=True))
    op.add_column("employees", sa.Column("is_manager", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("employees", "is_manager")
    op.drop_column("employees", "pay_type")
    op.drop_column("employees", "hire_date")
