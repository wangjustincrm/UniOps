"""sales26_stock_moves — the Radiant inventory ledger (design §15):
one row per stock move, written only by the stock sync from the records Sales already keeps (944
rows, 945 rows, delivery, returns) and by a person's adjustment. Append-only; every balance is a sum.

Revision id "sales26_stock_moves" is 19 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op

revision = 'sales26_stock_moves'
down_revision = 'sales25_line_returned_ea'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sales_stock_moves',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('item_code', sa.String(50), nullable=False),
        sa.Column('lot', sa.String(50), nullable=True),
        sa.Column('expiry', sa.Date(), nullable=True),
        sa.Column('qty_ea', sa.Integer(), nullable=False),
        sa.Column('from_place', sa.String(20), nullable=False),
        sa.Column('to_place', sa.String(20), nullable=False),
        sa.Column('source_type', sa.String(20), nullable=False),
        sa.Column('source_key', sa.String(60), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('transfer_id', sa.Integer(), nullable=True),
        sa.Column('document_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_by_name', sa.String(255), nullable=True),
        sa.CheckConstraint('qty_ea > 0', name='ck_sales_stock_moves_qty'),
        sa.CheckConstraint('from_place <> to_place', name='ck_sales_stock_moves_places'),
        sa.UniqueConstraint('source_type', 'source_key', name='uq_sales_stock_moves_source'),
    )
    op.create_index('ix_sales_stock_moves_item_lot', 'sales_stock_moves', ['item_code', 'lot'])
    op.create_index('ix_sales_stock_moves_at', 'sales_stock_moves', ['at'])


def downgrade() -> None:
    op.drop_index('ix_sales_stock_moves_at', 'sales_stock_moves')
    op.drop_index('ix_sales_stock_moves_item_lot', 'sales_stock_moves')
    op.drop_table('sales_stock_moves')
