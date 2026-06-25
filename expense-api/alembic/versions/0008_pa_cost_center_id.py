"""Add cost_center_id to payment_applications (PA-DIR cost center binding).

PA-DIR previously had no explicit cost center field — it was implicit via the
old per-CC L1 catalog. After the budget-api migration (shared catalog),
PA-DIR captures cost_center_id explicitly so budget-api can identify which
CC's plan + ledger the PA hits when booked.

Idempotent: skips if column already exists.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    res = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
    ), {"t": table, "c": column})
    return bool(res.first())


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "payment_applications", "cost_center_id"):
        op.add_column(
            "payment_applications",
            sa.Column("cost_center_id", UUID(as_uuid=True), nullable=True, index=True),
        )


def downgrade() -> None:
    op.drop_column("payment_applications", "cost_center_id")
