"""sales20_from_945 — orders made from a 945 with no order (FR-OB-25c).

`sales_orders.source_type` also accepts FROM_945. Constraint only.

Revision id "sales20_from_945" is 16 characters (limit 32).
"""
from alembic import op

revision = 'sales20_from_945'
down_revision = 'sales19_carriers'
branch_labels = None
depends_on = None

NAME = 'ck_sales_orders_source_type'


def upgrade() -> None:
    op.drop_constraint(NAME, 'sales_orders', type_='check')
    op.create_check_constraint(NAME, 'sales_orders', "source_type IN ('SHOPIFY', 'EDI_850', 'MANUAL', 'FROM_945')")


def downgrade() -> None:
    op.drop_constraint(NAME, 'sales_orders', type_='check')
    op.create_check_constraint(NAME, 'sales_orders', "source_type IN ('SHOPIFY', 'EDI_850', 'MANUAL')")
