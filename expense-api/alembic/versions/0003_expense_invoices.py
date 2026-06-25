"""OA S3 — expense invoices tables.

Creates: expense_invoices, expense_invoice_lines

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-01
"""
import uuid
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'expense_invoices',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('file_mime_type', sa.String(100), nullable=False),
        sa.Column('file_data', sa.LargeBinary, nullable=False),
        sa.Column('file_size_bytes', sa.BigInteger, nullable=False),
        sa.Column('invoice_number', sa.String(100), nullable=True),
        sa.Column('vendor_id', UUID(as_uuid=True), nullable=True),
        sa.Column('vendor_name', sa.String(255), nullable=True),
        sa.Column('invoice_date', sa.Date, nullable=True),
        sa.Column('due_date', sa.Date, nullable=True),
        sa.Column('currency', sa.String(10), nullable=False, server_default='CAD'),
        sa.Column('subtotal', sa.Numeric(15, 2), nullable=False, server_default='0'),
        sa.Column('tax_amount', sa.Numeric(15, 2), nullable=False, server_default='0'),
        sa.Column('total_amount', sa.Numeric(15, 2), nullable=False, server_default='0'),
        sa.Column('ocr_raw', JSONB, nullable=True),
        sa.Column('ocr_confidence', sa.Numeric(5, 4), nullable=True),
        sa.Column('low_confidence_fields', JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('status', sa.String(20), nullable=False, server_default='uploaded'),
        sa.Column('confirmed_by', UUID(as_uuid=True), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('pa_id', UUID(as_uuid=True), nullable=True),
        sa.Column('pa_number', sa.String(30), nullable=True),
        sa.Column('created_by', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_expense_invoices_vendor_id', 'expense_invoices', ['vendor_id'])
    op.create_index('ix_expense_invoices_invoice_number', 'expense_invoices', ['invoice_number'])
    op.create_index('ix_expense_invoices_status', 'expense_invoices', ['status'])
    op.create_index('ix_expense_invoices_created_by', 'expense_invoices', ['created_by'])

    op.create_table(
        'expense_invoice_lines',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('invoice_id', UUID(as_uuid=True),
                  sa.ForeignKey('expense_invoices.id', ondelete='CASCADE'), nullable=False),
        sa.Column('line_number', sa.Integer, nullable=False),
        sa.Column('description', sa.String(500), nullable=False),
        sa.Column('quantity', sa.Numeric(12, 4), nullable=False, server_default='1'),
        sa.Column('unit_price', sa.Numeric(15, 2), nullable=False, server_default='0'),
        sa.Column('amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('tax_amount', sa.Numeric(15, 2), nullable=False, server_default='0'),
        sa.Column('budget_account_id', UUID(as_uuid=True), nullable=True),
        sa.Column('budget_account_code', sa.String(50), nullable=True),
        sa.Column('budget_account_name', sa.String(255), nullable=True),
    )
    op.create_index('ix_expense_invoice_lines_invoice_id', 'expense_invoice_lines', ['invoice_id'])


def downgrade() -> None:
    op.drop_table('expense_invoice_lines')
    op.drop_table('expense_invoices')
