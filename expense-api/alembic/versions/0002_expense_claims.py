"""OA S2 — expense claims tables.

Creates: expense_claims, expense_line_items, expense_trip_items,
         expense_attachments, expense_approval_events, expense_policy_config

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-30
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── expense_claims ────────────────────────────────────────────────────────
    op.create_table(
        'expense_claims',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('claim_number', sa.String(30), nullable=False),
        sa.Column('claim_type', sa.String(10), nullable=False),
        sa.Column('employee_id', UUID(as_uuid=True), nullable=False),
        sa.Column('employee_name', sa.String(255), nullable=False),
        sa.Column('department_id', UUID(as_uuid=True), nullable=True),
        sa.Column('department_name', sa.String(255), nullable=False, server_default=''),
        sa.Column('submission_date', sa.Date, nullable=False),
        sa.Column('currency', sa.String(10), nullable=False, server_default='CAD'),
        sa.Column('project_id', UUID(as_uuid=True), nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('purpose', sa.String(500), nullable=True),
        sa.Column('vehicle_description', sa.String(255), nullable=True),
        sa.Column('vehicle_owned_by', sa.String(20), nullable=True),
        sa.Column('total_km', sa.Numeric(10, 2), nullable=True),
        sa.Column('total_amount', sa.Numeric(15, 2), nullable=False, server_default='0'),
        sa.Column('tax_amount', sa.Numeric(15, 2), nullable=False, server_default='0'),
        sa.Column('net_amount', sa.Numeric(15, 2), nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
        sa.Column('approval_step_idx', sa.Integer, nullable=False, server_default='0'),
        sa.Column('is_over_budget', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_expense_claims_claim_number', 'expense_claims', ['claim_number'], unique=True)
    op.create_index('ix_expense_claims_claim_type', 'expense_claims', ['claim_type'])
    op.create_index('ix_expense_claims_employee_id', 'expense_claims', ['employee_id'])
    op.create_index('ix_expense_claims_status', 'expense_claims', ['status'])
    op.create_index('ix_expense_claims_created_by', 'expense_claims', ['created_by'])

    # ── expense_line_items ────────────────────────────────────────────────────
    op.create_table(
        'expense_line_items',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('claim_id', UUID(as_uuid=True),
                  sa.ForeignKey('expense_claims.id', ondelete='CASCADE'), nullable=False),
        sa.Column('line_number', sa.Integer, nullable=False),
        sa.Column('expense_date', sa.Date, nullable=False),
        sa.Column('description', sa.String(200), nullable=False),
        sa.Column('budget_account_id', UUID(as_uuid=True), nullable=False),
        sa.Column('budget_account_code', sa.String(50), nullable=False),
        sa.Column('budget_account_name', sa.String(255), nullable=False),
        sa.Column('cost_center_id', UUID(as_uuid=True), nullable=True),
        sa.Column('cost_center_name', sa.String(255), nullable=True),
        sa.Column('total_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('tax_amount', sa.Numeric(15, 2), nullable=False, server_default='0'),
        sa.Column('net_amount', sa.Numeric(15, 2), nullable=False),
    )
    op.create_index('ix_expense_line_items_claim_id', 'expense_line_items', ['claim_id'])

    # ── expense_trip_items ────────────────────────────────────────────────────
    op.create_table(
        'expense_trip_items',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('claim_id', UUID(as_uuid=True),
                  sa.ForeignKey('expense_claims.id', ondelete='CASCADE'), nullable=False),
        sa.Column('trip_number', sa.Integer, nullable=False),
        sa.Column('trip_date', sa.Date, nullable=False),
        sa.Column('from_location', sa.String(255), nullable=False),
        sa.Column('to_location', sa.String(255), nullable=False),
        sa.Column('purpose', sa.String(500), nullable=False),
        sa.Column('is_round_trip', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('distance_km', sa.Numeric(10, 2), nullable=False),
        sa.Column('rate_per_km', sa.Numeric(8, 4), nullable=False),
        sa.Column('amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('budget_account_id', UUID(as_uuid=True), nullable=True),
        sa.Column('budget_account_code', sa.String(50), nullable=True),
        sa.Column('budget_account_name', sa.String(255), nullable=True),
    )
    op.create_index('ix_expense_trip_items_claim_id', 'expense_trip_items', ['claim_id'])

    # ── expense_attachments ───────────────────────────────────────────────────
    op.create_table(
        'expense_attachments',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('claim_id', UUID(as_uuid=True),
                  sa.ForeignKey('expense_claims.id', ondelete='CASCADE'), nullable=False),
        sa.Column('file_id', sa.String(255), nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('file_size_bytes', sa.Integer, nullable=False, server_default='0'),
        sa.Column('mime_type', sa.String(100), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_expense_attachments_claim_id', 'expense_attachments', ['claim_id'])

    # ── expense_approval_events ───────────────────────────────────────────────
    op.create_table(
        'expense_approval_events',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('claim_id', UUID(as_uuid=True),
                  sa.ForeignKey('expense_claims.id', ondelete='CASCADE'), nullable=False),
        sa.Column('actor_id', UUID(as_uuid=True), nullable=False),
        sa.Column('actor_name', sa.String(255), nullable=False),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('comment', sa.Text, nullable=True),
        sa.Column('from_status', sa.String(20), nullable=False),
        sa.Column('to_status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_expense_approval_events_claim_id', 'expense_approval_events', ['claim_id'])

    # ── expense_policy_config ─────────────────────────────────────────────────
    op.create_table(
        'expense_policy_config',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('hst_rate', sa.Numeric(6, 4), nullable=False, server_default='0.1300'),
        sa.Column('mileage_rate_per_km', sa.Numeric(8, 4), nullable=False, server_default='0.7200'),
        sa.Column('mileage_budget_account_id', UUID(as_uuid=True), nullable=True),
        sa.Column('max_km_per_claim', sa.Integer, nullable=False, server_default='2000'),
        sa.Column('meal_breakfast_limit', sa.Numeric(8, 2), nullable=False, server_default='23.00'),
        sa.Column('meal_lunch_limit', sa.Numeric(8, 2), nullable=False, server_default='23.00'),
        sa.Column('meal_dinner_limit', sa.Numeric(8, 2), nullable=False, server_default='46.00'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_by', UUID(as_uuid=True), nullable=True),
    )

    # Seed the singleton policy row
    op.execute("""
        INSERT INTO expense_policy_config (id, hst_rate, mileage_rate_per_km, max_km_per_claim)
        VALUES (gen_random_uuid(), 0.1300, 0.7200, 2000)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table('expense_policy_config')
    op.drop_table('expense_approval_events')
    op.drop_table('expense_attachments')
    op.drop_table('expense_trip_items')
    op.drop_table('expense_line_items')
    op.drop_table('expense_claims')
