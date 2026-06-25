"""sod_rules + audit_log (Phase 0-B4, FIN-AUD-001/003/005)

audit_log is append-only — never UPDATE/DELETE (CRA retention ≥ 6 years).
SoD seed: self_payment (document creator / claimant cannot execute its own
payment), enforced by finance-api's unified payment executor.

Revision ID: 0001_sod_audit
Revises:
Create Date: 2026-06-11
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001_sod_audit"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sod = op.create_table(
        "sod_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("rule_code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("actor_email", sa.String(255), nullable=True),
        sa.Column("service", sa.String(20), nullable=False),
        sa.Column("action", sa.String(50), nullable=False, index=True),
        sa.Column("object_type", sa.String(30), nullable=True),
        sa.Column("object_id", UUID(as_uuid=True), nullable=True),
        sa.Column("detail", JSONB, nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.bulk_insert(sod, [{
        "id": uuid.uuid4(),
        "rule_code": "self_payment",
        "name": "Creator cannot pay own document",
        "description": "The PA creator / expense claimant cannot execute the payment "
                       "of their own document (FIN-AUD-003). Enforced by the unified "
                       "payment executor.",
        "enabled": True,
        "entity_id": None,
    }])


def downgrade():
    op.drop_table("audit_log")
    op.drop_table("sod_rules")
