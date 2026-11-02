"""sales12_transfers — stock transfers, their 943 and 944 (P7; FR-OB-27, 80 … 84).

- `sales_warehouses`: the 943's party fields (FR-OB-27: no hard-coded
  addresses) — EDI id qualifier and id, address, city, province, postal code,
  country.
- `sales_transfer_number_seq`: the 5-digit transfer number (00104); P9 moves
  it above Zoho's highest.
- `sales_transfers` (+ `status_before_hold`, the On Hold of a second 944),
  `sales_transfer_lines` (EA requested and received, line status).
- `sales_receipts`: one per 944 document (applied false for a second,
  different one; shadow for one read during the shadow run) with the
  outcome and the discrepancies; `sales_receipt_rows`: every W07 by position,
  never overwritten, with the transfer line it was allocated to.

Revision id "sales12_transfers" is 17 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'sales12_transfers'
down_revision = 'sales11_invoicing'
branch_labels = None
depends_on = None

STATUSES = ('Draft', 'Ready To Send', '943 Generated', 'Waiting for 997', 'Waiting for 944', 'Received',
            'Partially Received', 'Discrepancy', 'Failed', 'Cancelled', 'On Hold')
LINE_STATUSES = ('Open', 'Partially Received', 'Received', 'Discrepancy')


def _audit():
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_name', sa.String(length=255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    ]


def _in(col, values, name):
    return sa.CheckConstraint(f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")", name=name)


def upgrade() -> None:
    for name, type_ in (('edi_id_qualifier', sa.String(5)), ('edi_id', sa.String(40)), ('address1', sa.String(255)),
                        ('city', sa.String(100)), ('province', sa.String(10)), ('zip', sa.String(20)), ('country', sa.String(5))):
        op.add_column('sales_warehouses', sa.Column(name, type_, nullable=True))
    op.execute(sa.schema.CreateSequence(sa.Sequence('sales_transfer_number_seq', start=1, minvalue=1, maxvalue=99999)))
    op.create_table(
        'sales_transfers',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('transfer_number', sa.String(length=10), nullable=False),
        sa.Column('from_warehouse_id', sa.Integer(), nullable=False),
        sa.Column('to_warehouse_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('status_before_hold', sa.String(length=30), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('document_id', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        *_audit(),
        _in('status', STATUSES, 'ck_sales_transfers_status'),
        sa.ForeignKeyConstraint(['from_warehouse_id'], ['sales_warehouses.id']),
        sa.ForeignKeyConstraint(['to_warehouse_id'], ['sales_warehouses.id']),
        sa.ForeignKeyConstraint(['document_id'], ['sales_documents.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('transfer_number', name='uq_sales_transfers_number'),
    )
    op.create_table(
        'sales_transfer_lines',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('transfer_id', sa.Integer(), nullable=False),
        sa.Column('line_no', sa.Integer(), nullable=False),
        sa.Column('item_code', sa.String(length=50), nullable=False),
        sa.Column('qty_ea', sa.Integer(), nullable=False),
        sa.Column('received_ea', sa.Integer(), server_default='0', nullable=False),
        sa.Column('line_status', sa.String(length=20), server_default='Open', nullable=False),
        _in('line_status', LINE_STATUSES, 'ck_sales_transfer_lines_status'),
        sa.ForeignKeyConstraint(['transfer_id'], ['sales_transfers.id']),
        sa.ForeignKeyConstraint(['item_code'], ['sales_items.item_code']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('transfer_id', 'line_no', name='uq_sales_transfer_lines_line'),
    )
    op.create_table(
        'sales_receipts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('transfer_id', sa.Integer(), nullable=True),
        sa.Column('transfer_key_received', sa.String(length=20), nullable=True),
        sa.Column('receipt_date', sa.Date(), nullable=True),
        sa.Column('total_ea', sa.Integer(), nullable=True),
        sa.Column('applied', sa.Boolean(), nullable=False),
        sa.Column('shadow', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('outcome', sa.String(length=30), nullable=True),
        sa.Column('discrepancies', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        *_audit()[:3],
        sa.ForeignKeyConstraint(['document_id'], ['sales_documents.id']),
        sa.ForeignKeyConstraint(['transfer_id'], ['sales_transfers.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', name='uq_sales_receipts_document'),
    )
    op.create_table(
        'sales_receipt_rows',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('receipt_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('lx_number', sa.String(length=20), nullable=True),
        sa.Column('item_code', sa.String(length=50), nullable=True),
        sa.Column('upc', sa.String(length=20), nullable=True),
        sa.Column('lot', sa.String(length=50), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('qty_ea', sa.Integer(), nullable=False),
        sa.Column('transfer_line_no', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['receipt_id'], ['sales_receipts.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('receipt_id', 'position', name='uq_sales_receipt_rows_position'),
    )


def downgrade() -> None:
    op.drop_table('sales_receipt_rows')
    op.drop_table('sales_receipts')
    op.drop_table('sales_transfer_lines')
    op.drop_table('sales_transfers')
    op.execute(sa.schema.DropSequence(sa.Sequence('sales_transfer_number_seq')))
    for name in ('country', 'zip', 'province', 'city', 'address1', 'edi_id', 'edi_id_qualifier'):
        op.drop_column('sales_warehouses', name)
