"""sales30_stock_counts — Radiant's inventory report kept as a count (user 2026-11-13): its moment, file and
lines (item, lot, expiry, quantity, on hold). Applied, Sales writes "count" moves at that moment so each lot
starts from Radiant's figure; the moves before are kept.

Revision id "sales30_stock_counts" is 20 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op

revision = 'sales30_stock_counts'
down_revision = 'sales29_order_agreement_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sales_stock_counts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('counted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(100), nullable=False, server_default='Radiant portal'),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('file_name', sa.String(200), nullable=True),
        sa.Column('document_id', sa.Integer(), sa.ForeignKey('sales_documents.id'), nullable=True),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('applied_by_name', sa.String(255), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_name', sa.String(255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_name', sa.String(255), nullable=True),
    )
    op.create_table(
        'sales_stock_count_lines',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('count_id', sa.Integer(), sa.ForeignKey('sales_stock_counts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('item_code', sa.String(50), nullable=False),
        sa.Column('lot', sa.String(50), nullable=True),
        sa.Column('expiry', sa.Date(), nullable=True),
        sa.Column('qty_ea', sa.Integer(), nullable=False),
        sa.Column('hold_ea', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('description', sa.String(255), nullable=True),
    )
    op.create_index('ix_sales_stock_count_lines_count', 'sales_stock_count_lines', ['count_id'])


def downgrade() -> None:
    op.execute("DELETE FROM sales_stock_moves WHERE source_type = 'count'")
    op.drop_index('ix_sales_stock_count_lines_count', 'sales_stock_count_lines')
    op.drop_table('sales_stock_count_lines')
    op.drop_table('sales_stock_counts')
