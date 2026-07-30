"""add paid_at to payment_applications + backfill

Dashboards ("Paid This Month" / "Processed This Month") used PaymentApplication
.updated_at as a proxy for the payment date. updated_at is onupdate=now(), so any
unrelated write (invoice-link rebuild, status re-sync, edit, import) bumps it —
which dragged the entire back-catalogue of processed PAs into "this month" (a
one-off invoice rebuild pass on 2026-07-21 alone inflated it by ~$15.3M).

Add a dedicated `paid_at` that is set once when a PA is paid (finance-api's
payment executor, and the zero-cash settlement path) and never moved by
onupdate. Dashboards key on it instead.

Backfill for existing processed PAs (paid_at IS NULL):
  * the real finance payment_records.payment_date when present (PAs actually
    paid through the system since go-live), else
  * created_at — the imported PMS "Created" date, which (unlike updated_at) was
    never corrupted by the rebuild pass.
payment_records is a finance-api table in the same physical DB; guard with
to_regclass so test DBs that lack the finance schema fall back cleanly.

Revision ID: ad_add_paid_at_to_pa
Revises: ac_add_department_id_to_pr
Create Date: 2026-07-29
"""
import sqlalchemy as sa
from alembic import op

revision = "ad_add_paid_at_to_pa"
down_revision = "ac_add_department_id_to_pr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_applications",
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.payment_records') IS NOT NULL THEN
                UPDATE payment_applications pa
                SET paid_at = COALESCE(
                    (SELECT max(pr.payment_date)::timestamptz
                     FROM payment_records pr WHERE pr.pa_id = pa.id),
                    pa.created_at)
                WHERE pa.status = 'processed' AND pa.paid_at IS NULL;
            ELSE
                UPDATE payment_applications pa
                SET paid_at = pa.created_at
                WHERE pa.status = 'processed' AND pa.paid_at IS NULL;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.drop_column("payment_applications", "paid_at")
