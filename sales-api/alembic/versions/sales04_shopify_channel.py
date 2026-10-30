"""sales04_shopify_channel — the Shopify channel's settings on its connection row (P3).

Option 1 (product owner, 2026-10-29): a connection row is the one place to look
for a channel; only secrets stay in the environment. For Shopify: host (the
store), api_version (was the SHOPIFY_API_VERSION setting), cursor_updated_at
(the inclusive updated_at cursor of FR-OB-10, was SHOPIFY_LAST_SYNC_DATE;
written by the intake, reset by an audited action; NULL pulls nothing),
page_size (≤ 250), order_number_cutoff (FR-OB-17) and partner_code (whose
orders the store's are). partner_code is for SHOPIFY rows only — an SFTP
channel's partner comes from each file (850 TradingPartner).

Revision id "sales04_shopify_channel" is 23 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa

revision = 'sales04_shopify_channel'
down_revision = 'sales03_documents_runs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sales_connections', sa.Column('api_version', sa.String(length=20), nullable=True))
    op.add_column('sales_connections', sa.Column('cursor_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('sales_connections', sa.Column('page_size', sa.Integer(), nullable=True))
    op.add_column('sales_connections', sa.Column('order_number_cutoff', sa.Integer(), nullable=True))
    op.add_column('sales_connections', sa.Column('partner_code', sa.String(length=50), nullable=True))
    op.create_foreign_key('fk_sales_connections_partner_code', 'sales_connections', 'sales_partners',
                          ['partner_code'], ['code'])
    op.create_check_constraint('ck_sales_connections_partner_shopify_only', 'sales_connections',
                               "kind = 'SHOPIFY' OR partner_code IS NULL")
    op.create_check_constraint('ck_sales_connections_page_size', 'sales_connections',
                               'page_size IS NULL OR page_size BETWEEN 1 AND 250')


def downgrade() -> None:
    op.drop_constraint('ck_sales_connections_page_size', 'sales_connections', type_='check')
    op.drop_constraint('ck_sales_connections_partner_shopify_only', 'sales_connections', type_='check')
    op.drop_constraint('fk_sales_connections_partner_code', 'sales_connections', type_='foreignkey')
    for col in ('partner_code', 'order_number_cutoff', 'page_size', 'cursor_updated_at', 'api_version'):
        op.drop_column('sales_connections', col)
