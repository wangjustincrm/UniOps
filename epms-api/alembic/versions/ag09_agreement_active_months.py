"""special_monthly cycle: bill only in the months that were ticked.

Raised by the user: a seasonal service (snow removal, lawn care, HVAC
servicing) is invoiced monthly but only runs for part of the year. `monthly`
generates a period for every calendar month, so the off-season months sit
`pending` forever, get flipped by the overdue sweep, and email the owner daily
— the same failure mode ag08 fixed at the front of the schedule, except this
one repeats every year. `yearly` throws away the monthly granularity instead.

`active_months` is that month selection: a JSONB array of 1..12. The period
grid is still the monthly one (labels `YYYY-MM`, invoice day clamped to short
months); only months outside the selection are dropped. A 3-year agreement
with May..November ticked generates 7 periods a year, 21 in total.

NULL means "not a special_monthly agreement" — every existing row keeps that,
and nothing is backfilled: the other four cycles have no month selection to
express.

Also widens `recurring_type` from VARCHAR(10) to VARCHAR(20). 'special_monthly'
is 15 characters; on VARCHAR(10) Postgres rejects the INSERT outright (22001
value too long) rather than truncating, so the column has to grow with it.

Revision ID: ag09_agreement_active_months
Revises: am02_po_signoff
Create Date: 2026-09-02
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ag09_agreement_active_months"
down_revision = "am02_po_signoff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "purchase_agreements", "recurring_type",
        existing_type=sa.String(length=10), type_=sa.String(length=20),
        existing_nullable=True,
    )
    op.add_column(
        "purchase_agreements",
        sa.Column("active_months", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True),
    )


def downgrade() -> None:
    op.drop_column("purchase_agreements", "active_months")
    # Any special_monthly row would not fit back into VARCHAR(10); clear the
    # cycle on those rows first so the shrink cannot fail half-way through.
    op.execute(
        "UPDATE purchase_agreements SET recurring_type = NULL "
        "WHERE recurring_type = 'special_monthly'"
    )
    op.alter_column(
        "purchase_agreements", "recurring_type",
        existing_type=sa.String(length=20), type_=sa.String(length=10),
        existing_nullable=True,
    )
