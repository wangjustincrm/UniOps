"""posting dimensions + entity_id + fiscal_periods (Phase 0-B1.7)

FIN-GL-003 seven-dimension accounting columns must exist BEFORE GL replay —
historical events cannot be backfilled with dimensions they never recorded.
entity_id (nullable, single-entity default) is the FIN-MD-001 multi-entity
pre-seed: every new finance table carries it from now on.

Revision ID: 0004_dims_periods
Revises: 0003_generalize_payments
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0004_dims_periods"
down_revision = "0003_generalize_payments"
branch_labels = None
depends_on = None


def upgrade():
    # posting_lines: remaining dimensions (cost_center_id / partner_id exist since 0002)
    op.add_column("posting_lines", sa.Column("item_id", UUID(as_uuid=True), nullable=True))
    op.add_column("posting_lines", sa.Column("lot_id", UUID(as_uuid=True), nullable=True))
    op.add_column("posting_lines", sa.Column("warehouse_id", UUID(as_uuid=True), nullable=True))
    op.add_column("posting_lines", sa.Column("project_id", UUID(as_uuid=True), nullable=True))
    op.add_column("posting_lines", sa.Column("channel", sa.String(30), nullable=True))
    op.add_column("posting_lines", sa.Column("entity_id", UUID(as_uuid=True), nullable=True))

    op.add_column("posting_events", sa.Column("entity_id", UUID(as_uuid=True), nullable=True))
    op.add_column("posting_events", sa.Column("fiscal_period", sa.String(7), nullable=True))
    op.create_index("ix_posting_events_fiscal_period", "posting_events", ["fiscal_period"])

    op.add_column("payment_records", sa.Column("entity_id", UUID(as_uuid=True), nullable=True))

    op.create_table(
        "fiscal_periods",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("period", sa.String(7), nullable=False, unique=True),  # 'YYYY-MM'
        # single-entity for now; multi-entity will move uniqueness to (entity_id, period)
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('open', 'soft_closed', 'hard_closed')",
                           name="ck_fiscal_periods_status"),
    )


def downgrade():
    op.drop_table("fiscal_periods")
    op.drop_column("payment_records", "entity_id")
    op.drop_index("ix_posting_events_fiscal_period", table_name="posting_events")
    op.drop_column("posting_events", "fiscal_period")
    op.drop_column("posting_events", "entity_id")
    for col in ("entity_id", "channel", "project_id", "warehouse_id", "lot_id", "item_id"):
        op.drop_column("posting_lines", col)
