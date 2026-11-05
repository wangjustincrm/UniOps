"""Join the Sales and Freight permission branches, both built on 0022.

Revision ID: 0024_merge_sales_freight
Revises: 0023_sales_perms, 0023_freight_perms
"""
revision = "0024_merge_sales_freight"
down_revision = ("0023_sales_perms", "0023_freight_perms")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
