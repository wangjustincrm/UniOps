"""sales32_gateway_settlements — what the payment gateways say about the money (Finance, user 2026-11-17).

Authorize.Net first (its Transaction Reporting API, read-only), PayPal later in the same tables:
`sales_gateway_settlements` (one deposit: a settled batch) and `sales_gateway_transactions` (one
gateway transaction, its batch, and the Shopify payment it was matched to). A connection learns
the kind AUTHORIZE_NET; a document the system AUTHORIZE_NET and the kind GATEWAY_JSON (the
gateway's responses, stored as received minus card and personal data).

Revision id "sales32_gateway_settlements" is 26 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = 'sales32_gateway_settlements'
down_revision = 'sales31_count_line_picked'
branch_labels = None
depends_on = None

OLD_CONN_KINDS = ("SFTP", "SHOPIFY")
OLD_DOC_KINDS = ("850_JSON", "940_EDI", "943_EDI", "944_EDI", "945_EDI", "997_EDI", "810_JSON", "INVOICE_EMAIL",
                 "SHOPIFY_ORDER_JSON", "OTHER", "ATTACHMENT")
OLD_SYSTEMS = ("RADIANT", "TRUECOMMERCE", "SHOPIFY", "EMAIL")


def _ck(table: str, name: str, column: str, values) -> None:
    op.drop_constraint(name, table, type_='check')
    op.create_check_constraint(name, table, f"{column} IN ({', '.join(repr(v) for v in values)})")


def _audited() -> list[sa.Column]:
    return [
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by', UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_name', sa.String(255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_name', sa.String(255), nullable=True),
    ]


def upgrade() -> None:
    _ck('sales_connections', 'ck_sales_connections_kind', 'kind', OLD_CONN_KINDS + ("AUTHORIZE_NET",))
    _ck('sales_documents', 'ck_sales_documents_kind', 'kind', OLD_DOC_KINDS + ("GATEWAY_JSON",))
    _ck('sales_documents', 'ck_sales_documents_system', 'system', OLD_SYSTEMS + ("AUTHORIZE_NET",))

    op.create_table(
        'sales_gateway_settlements', *_audited(),
        sa.Column('connection_id', sa.Integer(), sa.ForeignKey('sales_connections.id'), nullable=False),
        sa.Column('gateway', sa.String(20), nullable=False),
        sa.Column('external_id', sa.String(64), nullable=False),
        sa.Column('settled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state', sa.String(40), nullable=True),
        sa.Column('payment_method', sa.String(40), nullable=True),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('sales_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('sales_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('refund_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('refund_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('fee_amount', sa.Numeric(14, 2), nullable=True),
        sa.Column('net_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('declined_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('voided_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('chargeback_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('chargeback_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('returned_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('returned_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('transactions_read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('raw', JSONB(), nullable=False),
        sa.UniqueConstraint('gateway', 'external_id', name='uq_sales_gateway_settlements_external'),
        sa.CheckConstraint("gateway IN ('AUTHORIZE_NET')", name='ck_sales_gateway_settlements_gateway'),
    )
    op.create_index('ix_sales_gateway_settlements_settled_at', 'sales_gateway_settlements', ['settled_at'])

    op.create_table(
        'sales_gateway_transactions', *_audited(),
        sa.Column('connection_id', sa.Integer(), sa.ForeignKey('sales_connections.id'), nullable=False),
        sa.Column('gateway', sa.String(20), nullable=False),
        sa.Column('external_id', sa.String(64), nullable=False),
        sa.Column('settlement_id', sa.Integer(), sa.ForeignKey('sales_gateway_settlements.id'), nullable=True),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('status', sa.String(40), nullable=True),
        sa.Column('settled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('fee_amount', sa.Numeric(14, 2), nullable=True),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('card_type', sa.String(30), nullable=True),
        sa.Column('invoice_number', sa.String(64), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('ref_external_id', sa.String(64), nullable=True),
        sa.Column('details_read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('payment_row_id', sa.Integer(), sa.ForeignKey('sales_order_payments.id'), nullable=True),
        sa.Column('match_method', sa.String(20), nullable=True),
        sa.Column('matched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('raw', JSONB(), nullable=False),
        sa.UniqueConstraint('gateway', 'external_id', name='uq_sales_gateway_transactions_external'),
        sa.CheckConstraint("gateway IN ('AUTHORIZE_NET')", name='ck_sales_gateway_transactions_gateway'),
        sa.CheckConstraint("kind IN ('sale', 'refund', 'void', 'other')", name='ck_sales_gateway_transactions_kind'),
        sa.CheckConstraint("match_method IS NULL OR match_method IN ('TRANSACTION_ID', 'SHOPIFY_PAYMENT_ID', "
                           "'ORDER_NUMBER', 'AMOUNT_TIME')", name='ck_sales_gateway_transactions_match_method'),
    )
    op.create_index('uq_sales_gateway_transactions_payment', 'sales_gateway_transactions', ['payment_row_id'],
                    unique=True, postgresql_where=sa.text('payment_row_id IS NOT NULL'))
    op.create_index('ix_sales_gateway_transactions_settlement', 'sales_gateway_transactions', ['settlement_id'])
    op.create_index('ix_sales_gateway_transactions_submitted_at', 'sales_gateway_transactions', ['submitted_at'])


def downgrade() -> None:
    # Refuse before changing anything, never half-way: an Authorize.Net connection (or a document of
    # its system) would fail the narrower checks below. Remove them on purpose first.
    bind = op.get_bind()
    conns = bind.execute(sa.text("SELECT count(*) FROM sales_connections WHERE kind = 'AUTHORIZE_NET'")).scalar()
    docs = bind.execute(sa.text("SELECT count(*) FROM sales_documents WHERE system = 'AUTHORIZE_NET' "
                                "AND kind <> 'GATEWAY_JSON'")).scalar()
    if conns or docs:
        raise RuntimeError(f"sales32 downgrade refused: {conns} AUTHORIZE_NET connection(s) and {docs} other "
                           "AUTHORIZE_NET document(s) exist. Delete them (and their runs' references) first; "
                           "nothing was changed.")
    op.drop_table('sales_gateway_transactions')
    op.drop_table('sales_gateway_settlements')
    op.execute("DELETE FROM sales_documents WHERE kind = 'GATEWAY_JSON'")
    _ck('sales_documents', 'ck_sales_documents_system', 'system', OLD_SYSTEMS)
    _ck('sales_documents', 'ck_sales_documents_kind', 'kind', OLD_DOC_KINDS)
    _ck('sales_connections', 'ck_sales_connections_kind', 'kind', OLD_CONN_KINDS)
