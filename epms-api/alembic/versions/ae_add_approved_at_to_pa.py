"""add approved_at to payment_applications + backfill

Finance BP dashboard's "Approved Today" keyed on PaymentApplication.updated_at
(onupdate=now), so any later edit / in-flight re-sync bumped an approved PA into
"today". Add a dedicated approved_at, stamped once by the approval engine when a
PA reaches approved (same hasattr(doc,"approved_at") hook already used for
BudgetPlan / ExpenseClaim), and key the dashboard on it.

Backfill for PAs currently in status='approved': the timestamp of their most
recent approval_events row (the approve that took them there). approval_events
is shared-DB but owned by another service — guard with to_regclass so test DBs
that lack it are a clean no-op (those approved PAs simply carry NULL approved_at
and correctly stay out of "Approved Today").

Revision ID: ae_add_approved_at_to_pa
Revises: ad_add_paid_at_to_pa
Create Date: 2026-07-29
"""
import sqlalchemy as sa
from alembic import op

revision = "ae_add_approved_at_to_pa"
down_revision = "ad_add_paid_at_to_pa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_applications",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.approval_events') IS NOT NULL THEN
                UPDATE payment_applications pa
                SET approved_at = ev.ts
                FROM (
                    SELECT document_id, max(created_at) AS ts
                    FROM approval_events
                    WHERE document_type IN ('pa', 'pa_dir')
                    GROUP BY document_id
                ) ev
                WHERE ev.document_id = pa.id
                  AND pa.status = 'approved'
                  AND pa.approved_at IS NULL;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.drop_column("payment_applications", "approved_at")
