"""Join the Sales branch (0024_merge_sales_freight) and main's employee / HR permissions (0025_hr_role).

Revision ID: 0026_merge_sales_hr
Revises: 0024_merge_sales_freight, 0025_hr_role
"""
revision = "0026_merge_sales_hr"
down_revision = ("0024_merge_sales_freight", "0025_hr_role")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
