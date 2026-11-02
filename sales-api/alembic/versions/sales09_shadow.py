"""sales09_shadow — the shadow run (design §5.4; user decision 2026-11-02).

- `sales_documents.shadow`: read while the shadow run was on — never drives a
  status change or a Shopify write (P5 reads it for 945s / 944s).
- `sales_orders.shadow`: received while the shadow run was on — Zoho handles
  it; Sales never sends it a 940 by itself, also after the switch-off.
- `sales_shadow_matches`: a Zoho reference 940 against the 940 Sales would
  send (dry run), segment by segment, or a Sales order no reference matched.

Revision id "sales09_shadow" is 14 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'sales09_shadow'
down_revision = 'sales08_outbound_940'
branch_labels = None
depends_on = None

STATUSES = ('Match', 'Differs', 'Expected difference', 'No Sales order', 'Sales only', 'Unreadable')


def upgrade() -> None:
    op.add_column('sales_documents', sa.Column('shadow', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('sales_orders', sa.Column('shadow', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        'sales_shadow_matches',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('reference_document_id', sa.Integer(), nullable=True),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('reference_number', sa.String(length=100), nullable=True),
        sa.Column('partner_code', sa.String(length=50), nullable=True),
        sa.Column('sales_text', sa.Text(), nullable=True),
        sa.Column('rows', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('differences', sa.Integer(), nullable=False),
        sa.Column('diff_signature', sa.String(length=64), nullable=True),
        sa.Column('expected_signature', sa.String(length=64), nullable=True),
        sa.Column('expected_reason', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('compared_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_name', sa.String(length=255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_name', sa.String(length=255), nullable=True),
        sa.CheckConstraint("status IN (" + ", ".join(f"'{s}'" for s in STATUSES) + ")",
                           name='ck_sales_shadow_matches_status'),
        sa.ForeignKeyConstraint(['reference_document_id'], ['sales_documents.id']),
        sa.ForeignKeyConstraint(['order_id'], ['sales_orders.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('uq_sales_shadow_matches_reference', 'sales_shadow_matches', ['reference_document_id'],
                    unique=True, postgresql_where=sa.text('reference_document_id IS NOT NULL'))
    op.create_index('uq_sales_shadow_matches_sales_only', 'sales_shadow_matches', ['order_id'], unique=True,
                    postgresql_where=sa.text('reference_document_id IS NULL'))


def downgrade() -> None:
    op.drop_index('uq_sales_shadow_matches_sales_only', table_name='sales_shadow_matches')
    op.drop_index('uq_sales_shadow_matches_reference', table_name='sales_shadow_matches')
    op.drop_table('sales_shadow_matches')
    op.drop_column('sales_orders', 'shadow')
    op.drop_column('sales_documents', 'shadow')
