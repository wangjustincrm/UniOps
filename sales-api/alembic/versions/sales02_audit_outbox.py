"""sales02_audit_outbox — the platform's audit and outbox tables (design §13.7 / §13.13).

Also the case-insensitive partner code: unique index uq_sales_partners_code_ci
on lower(code). The exact unique key from sales01 stays — the DC routing and
partner-code foreign keys reference it, and an expression index cannot.

sales_audit: one row per change to an Audited entity and per state transition
(who, what, before / after, reason — FR-OB-53); P1's configuration writes are
the first to be audited. sales_outbox: external effects written in the
business transaction and executed after commit (at most once). Columns are
the platform mixins' (app/platform/audit.py, outbox.py). Spec §3.6 renumbered
(product owner, 2026-10-29): documents / runs / log become sales03, and so on.
Generated from app/models/records.py and reviewed; `alembic check` in
tests/test_alembic_scope.py proves model and migration agree.

Revision id "sales02_audit_outbox" is 20 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'sales02_audit_outbox'
down_revision = 'sales01_config'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('sales_audit',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('user_name', sa.String(length=255), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('entity', sa.String(length=100), nullable=False),
    sa.Column('entity_id', sa.String(length=100), nullable=True),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sales_audit_entity', 'sales_audit', ['entity', 'entity_id', 'at'], unique=False)
    op.create_table('sales_outbox',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('entity_type', sa.String(length=100), nullable=True),
    sa.Column('entity_id', sa.String(length=100), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('manual', sa.Boolean(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sales_outbox_entity_id'), 'sales_outbox', ['entity_id'], unique=False)
    op.create_index(op.f('ix_sales_outbox_entity_type'), 'sales_outbox', ['entity_type'], unique=False)
    op.create_index(op.f('ix_sales_outbox_kind'), 'sales_outbox', ['kind'], unique=False)
    op.create_index(op.f('ix_sales_outbox_status'), 'sales_outbox', ['status'], unique=False)
    # Both stay: sales_partners_code_key is the FKs' target, this one makes codes unique ignoring case.
    op.create_index('uq_sales_partners_code_ci', 'sales_partners', [sa.text('lower(code)')], unique=True)


def downgrade() -> None:
    op.drop_index('uq_sales_partners_code_ci', table_name='sales_partners')
    op.drop_index(op.f('ix_sales_outbox_status'), table_name='sales_outbox')
    op.drop_index(op.f('ix_sales_outbox_kind'), table_name='sales_outbox')
    op.drop_index(op.f('ix_sales_outbox_entity_type'), table_name='sales_outbox')
    op.drop_index(op.f('ix_sales_outbox_entity_id'), table_name='sales_outbox')
    op.drop_table('sales_outbox')
    op.drop_index('ix_sales_audit_entity', table_name='sales_audit')
    op.drop_table('sales_audit')
