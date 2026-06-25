"""generalize payment_records beyond PA (expense claims, future AR/FA)

pa_id / vendor_id / vendor_name become nullable; doc_kind / doc_id /
doc_number identify the paid document generically. Existing rows are
backfilled as doc_kind='pa'.

Revision ID: 0003_generalize_payments
Revises: 0002_posting_events
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0003_generalize_payments"
down_revision = "0002_posting_events"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("payment_records", "pa_id", nullable=True)
    op.alter_column("payment_records", "pa_number", nullable=True)
    op.alter_column("payment_records", "vendor_id", nullable=True)
    op.alter_column("payment_records", "vendor_name", nullable=True)
    op.add_column("payment_records", sa.Column("doc_kind", sa.String(20), nullable=True))
    op.add_column("payment_records", sa.Column("doc_id", UUID(as_uuid=True), nullable=True))
    op.add_column("payment_records", sa.Column("doc_number", sa.String(40), nullable=True))
    op.execute(
        "UPDATE payment_records SET doc_kind = 'pa', doc_id = pa_id, doc_number = pa_number"
        " WHERE doc_kind IS NULL"
    )
    op.create_index("ix_payment_records_doc", "payment_records", ["doc_kind", "doc_id"])


def downgrade():
    op.drop_index("ix_payment_records_doc", table_name="payment_records")
    op.drop_column("payment_records", "doc_number")
    op.drop_column("payment_records", "doc_id")
    op.drop_column("payment_records", "doc_kind")
    op.alter_column("payment_records", "vendor_name", nullable=False)
    op.alter_column("payment_records", "vendor_id", nullable=False)
    op.alter_column("payment_records", "pa_number", nullable=False)
    op.alter_column("payment_records", "pa_id", nullable=False)
