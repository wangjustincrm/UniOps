"""sales06_unit_shopify_titles — Shopify titles (aliases) per item unit (P3).

A Shopify variant title such as "2-Can" or "2-Pack" does not contain the unit
name 2PACK, so the Item Master names the titles that mean each unit: a comma
list, matched like the unit's own name (PRD A.11; the Item Master decides).
NULL = the unit's name only.

Revision id "sales06_unit_shopify_titles" is 27 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa

revision = 'sales06_unit_shopify_titles'
down_revision = 'sales05_orders'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sales_item_units', sa.Column('shopify_titles', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('sales_item_units', 'shopify_titles')
