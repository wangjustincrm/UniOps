"""sales03_documents_runs — the document store, runs and log (design §3.2 / §3.6, P2).

sales_documents (FR-OB-05, -70..76): original bytes kept, never updated or
deleted. Two identities (FR-OB-71): uq_sales_documents_remote_file on the
inbound remote file (connection, name, size, mtime), and uq_sales_documents_sha256
on the bytes — partial, because a Duplicate or a "not ours" (Skipped) file is
recorded as an identity row WITHOUT content (product owner, 2026-10-29), so an
unchanged folder downloads nothing on the next pass (AC-OB-04). Only those two
statuses may lack content (ck_sales_documents_content).
sales_document_links, sales_runs (platform run record), sales_log (FR-OB-53;
order / transfer ids get their foreign keys with those tables).
sales_connections.host_key: the pinned SSH host key (§5.1; no pin, no connection).

Revision id "sales03_documents_runs" is 21 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'sales03_documents_runs'
down_revision = 'sales02_audit_outbox'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('sales_runs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('job', sa.String(length=40), nullable=False),
    sa.Column('trigger', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')", name='ck_sales_runs_status'),
    sa.CheckConstraint("trigger IN ('SCHEDULE', 'MANUAL')", name='ck_sales_runs_trigger'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sales_runs_job'), 'sales_runs', ['job'], unique=False)
    op.create_index(op.f('ix_sales_runs_status'), 'sales_runs', ['status'], unique=False)
    op.create_table('sales_documents',
    sa.Column('direction', sa.String(length=3), nullable=False),
    sa.Column('origin', sa.String(length=20), nullable=False),
    sa.Column('system', sa.String(length=20), nullable=True),
    sa.Column('connection_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('remote_name', sa.Text(), nullable=True),
    sa.Column('size', sa.Integer(), nullable=True),
    sa.Column('remote_mtime', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('content', sa.LargeBinary(), nullable=True),
    sa.Column('body_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('processing_status', sa.String(length=20), nullable=True),
    sa.Column('file_status', sa.String(length=20), nullable=True),
    sa.Column('ack_status', sa.String(length=20), nullable=True),
    sa.Column('isa13', sa.String(length=9), nullable=True),
    sa.Column('gs06', sa.String(length=9), nullable=True),
    sa.Column('st02', sa.String(length=9), nullable=True),
    sa.Column('partner_code', sa.String(length=50), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('replaced_by_id', sa.Integer(), nullable=True),
    sa.Column('zoho_file_id', sa.String(length=100), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_by_name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("ack_status IN ('Not Required', 'Waiting', 'Accepted', 'Accepted by 945', 'Rejected', 'Timeout')", name='ck_sales_documents_ack_status'),
    sa.CheckConstraint("content IS NOT NULL OR processing_status IN ('Duplicate', 'Skipped')", name='ck_sales_documents_content'),
    sa.CheckConstraint("direction IN ('IN', 'OUT')", name='ck_sales_documents_direction'),
    sa.CheckConstraint("file_status IN ('Ready To Send', 'Sending', 'Sent', 'Failed', 'Suppressed', 'Replaced', 'Cancelled')", name='ck_sales_documents_file_status'),
    sa.CheckConstraint("kind IN ('850_JSON', '940_EDI', '943_EDI', '944_EDI', '945_EDI', '997_EDI', '810_JSON', 'INVOICE_EMAIL', 'SHOPIFY_ORDER_JSON', 'OTHER')", name='ck_sales_documents_kind'),
    sa.CheckConstraint("origin IN ('PARTNER', 'UNIOPS', 'ZOHO_REFERENCE', 'ZOHO_MIGRATION')", name='ck_sales_documents_origin'),
    sa.CheckConstraint("processing_status IN ('Pending', 'Processing', 'Processed', 'Failed', 'Skipped', 'Duplicate')", name='ck_sales_documents_processing_status'),
    sa.CheckConstraint("system IN ('RADIANT', 'TRUECOMMERCE', 'SHOPIFY', 'EMAIL')", name='ck_sales_documents_system'),
    sa.ForeignKeyConstraint(['connection_id'], ['sales_connections.id'], ),
    sa.ForeignKeyConstraint(['replaced_by_id'], ['sales_documents.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sales_documents_created_at', 'sales_documents', ['created_at'], unique=False)
    op.create_index('uq_sales_documents_remote_file', 'sales_documents', ['connection_id', 'remote_name', 'size', 'remote_mtime'], unique=True, postgresql_where=sa.text("direction = 'IN'"))
    op.create_index('uq_sales_documents_sha256', 'sales_documents', ['sha256'], unique=True, postgresql_where=sa.text('content IS NOT NULL'))
    op.create_table('sales_document_links',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('entity_type', sa.String(length=20), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['sales_documents.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'entity_type', 'entity_id', name='uq_sales_document_links')
    )
    op.create_table('sales_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('level', sa.String(length=10), nullable=False),
    sa.Column('step', sa.String(length=40), nullable=False),
    sa.Column('connection_id', sa.Integer(), nullable=True),
    sa.Column('document_id', sa.Integer(), nullable=True),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('transfer_id', sa.Integer(), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint("level IN ('ERROR', 'WARNING', 'INFO')", name='ck_sales_log_level'),
    sa.ForeignKeyConstraint(['connection_id'], ['sales_connections.id'], ),
    sa.ForeignKeyConstraint(['document_id'], ['sales_documents.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['sales_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sales_log_at', 'sales_log', ['at'], unique=False)
    op.create_index(op.f('ix_sales_log_document_id'), 'sales_log', ['document_id'], unique=False)
    op.create_index(op.f('ix_sales_log_order_id'), 'sales_log', ['order_id'], unique=False)
    op.create_index(op.f('ix_sales_log_run_id'), 'sales_log', ['run_id'], unique=False)
    op.create_index(op.f('ix_sales_log_transfer_id'), 'sales_log', ['transfer_id'], unique=False)
    op.add_column('sales_connections', sa.Column('host_key', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('sales_connections', 'host_key')
    op.drop_index(op.f('ix_sales_log_transfer_id'), table_name='sales_log')
    op.drop_index(op.f('ix_sales_log_run_id'), table_name='sales_log')
    op.drop_index(op.f('ix_sales_log_order_id'), table_name='sales_log')
    op.drop_index(op.f('ix_sales_log_document_id'), table_name='sales_log')
    op.drop_index('ix_sales_log_at', table_name='sales_log')
    op.drop_table('sales_log')
    op.drop_table('sales_document_links')
    op.drop_index('uq_sales_documents_sha256', table_name='sales_documents', postgresql_where=sa.text('content IS NOT NULL'))
    op.drop_index('uq_sales_documents_remote_file', table_name='sales_documents', postgresql_where=sa.text("direction = 'IN'"))
    op.drop_index('ix_sales_documents_created_at', table_name='sales_documents')
    op.drop_table('sales_documents')
    op.drop_index(op.f('ix_sales_runs_status'), table_name='sales_runs')
    op.drop_index(op.f('ix_sales_runs_job'), table_name='sales_runs')
    op.drop_table('sales_runs')
