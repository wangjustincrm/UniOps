"""sales01_config — configuration tables (design §3.1 / §3.6).

partners, items, item units, partner codes, DC routing, warehouses,
connections, settings and notification rules. Closed value sets are CHECK
constraints (§3). No seed data (FR-OB-09): configuration arrives by CSV import.
Generated from app/models/config.py and reviewed; `alembic check` in
tests/test_alembic_scope.py proves model and migration agree.

First revision of this service: own version table alembic_version_sales,
down_revision None. Revision id "sales01_config" is 14 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'sales01_config'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('sales_connections',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('host', sa.String(length=255), nullable=True),
    sa.Column('port', sa.Integer(), nullable=True),
    sa.Column('username', sa.String(length=255), nullable=True),
    sa.Column('inbound_folder', sa.Text(), nullable=True),
    sa.Column('outbound_folder', sa.Text(), nullable=True),
    sa.Column('processed_folder', sa.Text(), nullable=True),
    sa.Column('error_folder', sa.Text(), nullable=True),
    sa.Column('test_inbound_folder', sa.Text(), nullable=True),
    sa.Column('test_outbound_folder', sa.Text(), nullable=True),
    sa.Column('after_read', sa.String(length=10), nullable=False),
    sa.Column('stable_seconds', sa.Integer(), server_default='60', nullable=False),
    sa.Column('mode', sa.String(length=10), nullable=False),
    sa.Column('auth_failed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('auth_failed_reason', sa.Text(), nullable=True),
    sa.Column('secret_name', sa.String(length=100), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_by_name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint("after_read IN ('LEAVE', 'MOVE', 'DELETE')", name='ck_sales_connections_after_read'),
    sa.CheckConstraint("kind IN ('SFTP', 'SHOPIFY')", name='ck_sales_connections_kind'),
    sa.CheckConstraint("mode IN ('LIVE', 'TEST')", name='ck_sales_connections_mode'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('sales_items',
    sa.Column('item_code', sa.String(length=50), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('sku', sa.String(length=50), nullable=True),
    sa.Column('upc', sa.String(length=50), nullable=True),
    sa.Column('gtin', sa.String(length=50), nullable=True),
    sa.Column('gross_weight_lb_per_ea', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('warehouse_held', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_by_name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('item_code')
    )
    op.create_table('sales_notification_rules',
    sa.Column('event_type', sa.String(length=100), nullable=False),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('recipients', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_by_name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint("channel IN ('TASK', 'EMAIL', 'BOTH')", name='ck_sales_notification_rules_channel'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_type')
    )
    op.create_table('sales_partners',
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('abbrev', sa.String(length=50), nullable=True),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('settlement_mode', sa.String(length=20), nullable=False),
    sa.Column('sending_mode', sa.String(length=20), nullable=False),
    sa.Column('delay_minutes', sa.Integer(), nullable=False),
    sa.Column('invoice_method', sa.String(length=20), nullable=False),
    sa.Column('invoice_email', sa.String(length=255), nullable=True),
    sa.Column('invoice_cc', sa.Text(), nullable=True),
    sa.Column('payment_terms', sa.String(length=100), nullable=True),
    sa.Column('dc_routing_required', sa.Boolean(), nullable=False),
    sa.Column('min_shelf_life_days', sa.Integer(), nullable=True),
    sa.Column('label_code', sa.String(length=50), nullable=True),
    sa.Column('carrier_defaults', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_by_name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint("channel IN ('SHOPIFY', 'TRUECOMMERCE', 'MANUAL')", name='ck_sales_partners_channel'),
    sa.CheckConstraint("invoice_method IN ('810_JSON', 'EMAIL', 'NONE')", name='ck_sales_partners_invoice_method'),
    sa.CheckConstraint("sending_mode IN ('AUTOMATIC', 'MANUAL')", name='ck_sales_partners_sending_mode'),
    sa.CheckConstraint("settlement_mode IN ('PREPAID', 'INVOICE')", name='ck_sales_partners_settlement_mode'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('sales_settings',
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_by_name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('sales_warehouses',
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('address_lines', sa.Text(), nullable=True),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_by_name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint("role IN ('SHIPPER', 'WAREHOUSE', 'TRANSFER_SOURCE')", name='ck_sales_warehouses_role'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('sales_dc_routing',
    sa.Column('partner_code', sa.String(length=50), nullable=False),
    sa.Column('ship_to_gln', sa.String(length=50), nullable=False),
    sa.Column('dc_number', sa.String(length=50), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_by_name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.ForeignKeyConstraint(['partner_code'], ['sales_partners.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('partner_code', 'ship_to_gln', name='uq_sales_dc_routing_partner_gln')
    )
    op.create_table('sales_item_partner_codes',
    sa.Column('partner_code', sa.String(length=50), nullable=False),
    sa.Column('buyer_part', sa.String(length=100), nullable=False),
    sa.Column('item_code', sa.String(length=50), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_by_name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['item_code'], ['sales_items.item_code'], ),
    sa.ForeignKeyConstraint(['partner_code'], ['sales_partners.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('partner_code', 'buyer_part', name='uq_sales_item_partner_codes_partner_part')
    )
    op.create_table('sales_item_units',
    sa.Column('item_code', sa.String(length=50), nullable=False),
    sa.Column('unit', sa.String(length=20), nullable=False),
    sa.Column('factor_ea', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_by_name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint("unit <> 'EA' OR factor_ea = 1", name='ck_sales_item_units_ea_is_one'),
    sa.ForeignKeyConstraint(['item_code'], ['sales_items.item_code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('item_code', 'unit', name='uq_sales_item_units_item_unit')
    )


def downgrade() -> None:
    op.drop_table('sales_item_units')
    op.drop_table('sales_item_partner_codes')
    op.drop_table('sales_dc_routing')
    op.drop_table('sales_warehouses')
    op.drop_table('sales_settings')
    op.drop_table('sales_partners')
    op.drop_table('sales_notification_rules')
    op.drop_table('sales_items')
    op.drop_table('sales_connections')
