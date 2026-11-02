"""sales10_shipments — 945 shipments, their rows, and the shadow matching of
997 / 945 against the Zoho references (P5).

- `sales_shipments`: one per 945 set (document, set index); order_id nullable
  (FR-OB-25c); `applied` false for a second, different 945 (FR-OB-25);
  `shadow` for a 945 read during the shadow run — never applied, never a
  Shopify write; `checks` the FR-OB-25d findings; the Shopify write-back
  state (FR-OB-11 / 11a).
- `sales_shipment_rows`: every W12 under every LX (FR-OB-25b), keyed
  (shipment, LX position, W12 position) — never the tracking number (FR-OB-25).
- `sales_shadow_matches`: + `document_id` (the inbound 997 / 945 compared) and
  `would` (what Sales would have recorded); status "No reference"; one row per
  reference only for the 940 comparison.

Revision id "sales10_shipments" is 17 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'sales10_shipments'
down_revision = 'sales09_shadow'
branch_labels = None
depends_on = None

OLD = ('Match', 'Differs', 'Expected difference', 'No Sales order', 'Sales only', 'Unreadable')
NEW = OLD + ('No reference',)
SHOPIFY = ('Not needed', 'Pending', 'Done', 'Failed', 'Suppressed')


def _audit_cols():
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_name', sa.String(length=255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        'sales_shipments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('set_index', sa.Integer(), nullable=False),
        sa.Column('set_control', sa.String(length=9), nullable=True),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('order_key_received', sa.String(length=40), nullable=True),
        sa.Column('ship_date', sa.Date(), nullable=True),
        sa.Column('ship_date_source', sa.String(length=5), nullable=True),
        sa.Column('carrier', sa.String(length=40), nullable=True),
        sa.Column('mode', sa.String(length=10), nullable=True),
        sa.Column('tracking', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('bol', sa.String(length=80), nullable=True),
        sa.Column('carrier_ref', sa.String(length=80), nullable=True),
        sa.Column('ship_to', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('applied', sa.Boolean(), nullable=False),
        sa.Column('shadow', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('shopify_status', sa.String(length=20), nullable=False),
        sa.Column('shopify_fulfillment_id', sa.String(length=40), nullable=True),
        sa.Column('shopify_error', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        *_audit_cols(),
        sa.CheckConstraint("shopify_status IN (" + ", ".join(f"'{s}'" for s in SHOPIFY) + ")",
                           name='ck_sales_shipments_shopify_status'),
        sa.ForeignKeyConstraint(['document_id'], ['sales_documents.id']),
        sa.ForeignKeyConstraint(['order_id'], ['sales_orders.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'set_index', name='uq_sales_shipments_document_set'),
    )
    op.create_index('ix_sales_shipments_order_id', 'sales_shipments', ['order_id'])
    op.create_table(
        'sales_shipment_rows',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('shipment_id', sa.Integer(), nullable=False),
        sa.Column('lx_index', sa.Integer(), nullable=False),
        sa.Column('w12_index', sa.Integer(), nullable=False),
        sa.Column('lx_number', sa.String(length=20), nullable=True),
        sa.Column('sscc', sa.String(length=20), nullable=True),
        sa.Column('item_code', sa.String(length=50), nullable=True),
        sa.Column('upc', sa.String(length=20), nullable=True),
        sa.Column('lot', sa.String(length=50), nullable=True),
        sa.Column('expiry', sa.Date(), nullable=True),
        sa.Column('qty_ordered_ea', sa.Integer(), nullable=True),
        sa.Column('qty_shipped_ea', sa.Integer(), nullable=False),
        sa.Column('weight', sa.Numeric(12, 3), nullable=True),
        sa.Column('tracking', sa.String(length=80), nullable=True),
        sa.Column('order_line_id', sa.Integer(), nullable=True),
        sa.Column('needs_review', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(['shipment_id'], ['sales_shipments.id']),
        sa.ForeignKeyConstraint(['order_line_id'], ['sales_order_lines.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('shipment_id', 'lx_index', 'w12_index', name='uq_sales_shipment_rows_position'),
    )
    op.add_column('sales_shadow_matches', sa.Column('document_id', sa.Integer(), nullable=True))
    op.add_column('sales_shadow_matches', sa.Column('would', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_foreign_key('fk_sales_shadow_matches_document', 'sales_shadow_matches', 'sales_documents',
                          ['document_id'], ['id'])
    op.create_index('uq_sales_shadow_matches_document', 'sales_shadow_matches', ['document_id'], unique=True,
                    postgresql_where=sa.text('document_id IS NOT NULL'))
    op.drop_index('uq_sales_shadow_matches_reference', table_name='sales_shadow_matches')
    op.create_index('uq_sales_shadow_matches_reference', 'sales_shadow_matches', ['reference_document_id'],
                    unique=True, postgresql_where=sa.text("reference_document_id IS NOT NULL AND kind = '940'"))
    op.drop_index('uq_sales_shadow_matches_sales_only', table_name='sales_shadow_matches')
    op.create_index('uq_sales_shadow_matches_sales_only', 'sales_shadow_matches', ['order_id'], unique=True,
                    postgresql_where=sa.text("reference_document_id IS NULL AND kind = '940'"))
    op.drop_constraint('ck_sales_shadow_matches_status', 'sales_shadow_matches', type_='check')
    op.create_check_constraint('ck_sales_shadow_matches_status', 'sales_shadow_matches',
                               "status IN (" + ", ".join(f"'{s}'" for s in NEW) + ")")


def downgrade() -> None:
    op.drop_constraint('ck_sales_shadow_matches_status', 'sales_shadow_matches', type_='check')
    op.create_check_constraint('ck_sales_shadow_matches_status', 'sales_shadow_matches',
                               "status IN (" + ", ".join(f"'{s}'" for s in OLD) + ")")
    op.drop_index('uq_sales_shadow_matches_sales_only', table_name='sales_shadow_matches')
    op.create_index('uq_sales_shadow_matches_sales_only', 'sales_shadow_matches', ['order_id'], unique=True,
                    postgresql_where=sa.text('reference_document_id IS NULL'))
    op.drop_index('uq_sales_shadow_matches_reference', table_name='sales_shadow_matches')
    op.create_index('uq_sales_shadow_matches_reference', 'sales_shadow_matches', ['reference_document_id'],
                    unique=True, postgresql_where=sa.text('reference_document_id IS NOT NULL'))
    op.drop_index('uq_sales_shadow_matches_document', table_name='sales_shadow_matches')
    op.drop_constraint('fk_sales_shadow_matches_document', 'sales_shadow_matches', type_='foreignkey')
    op.drop_column('sales_shadow_matches', 'would')
    op.drop_column('sales_shadow_matches', 'document_id')
    op.drop_index('ix_sales_shipments_order_id', table_name='sales_shipments')
    op.drop_table('sales_shipment_rows')
    op.drop_table('sales_shipments')
