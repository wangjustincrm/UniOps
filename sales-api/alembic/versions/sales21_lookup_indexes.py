"""sales21_lookup_indexes — indexes for the columns reads find rows by
(app/models/indexes.py). Indexes only; no data changes.

Revision id "sales21_lookup_indexes" is 22 characters (limit 32).
"""
from alembic import op

revision = 'sales21_lookup_indexes'
down_revision = 'sales20_from_945'
branch_labels = None
depends_on = None

INDEXES = (
    ('ix_sales_document_links_entity', 'sales_document_links', ['entity_type', 'entity_id']),
    ('ix_sales_orders_source_order_number', 'sales_orders', ['source_order_number']),
    ('ix_sales_orders_first_received_at', 'sales_orders', ['first_received_at']),
    ('ix_sales_transfers_document_id', 'sales_transfers', ['document_id']),
    ('ix_sales_receipts_transfer_id', 'sales_receipts', ['transfer_id']),
    ('ix_sales_invoices_document_id', 'sales_invoices', ['document_id']),
    ('ix_sales_shipment_rows_order_line_id', 'sales_shipment_rows', ['order_line_id']),
    ('ix_sales_invoice_lines_order_line_id', 'sales_invoice_lines', ['order_line_id']),
    ('ix_sales_order_refund_lines_order_line_id', 'sales_order_refund_lines', ['order_line_id']),
)


def upgrade() -> None:
    for name, table, cols in INDEXES:
        op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
