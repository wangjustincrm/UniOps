"""Denormalize agreement_type onto invoices — snapshot at match time.

Code review (Task 8, fix round 1, Important 1): the frontend needed to know
whether an invoice's linked agreement is house_account (the only type with
receipt evidence) purely to decide whether to render the Receipt Evidence
panel and its accompanying copy. It used to answer that by calling
`GET /invoices/{id}/agreement-candidates`, which is gated on
`_require_invoice_match_access` (AP roles ∪ uploader ∪ the holder of an open
match_invoice task) — every OTHER caller gets a 403, `agreement_type` reads
as `undefined`, and the page silently fell back to copy that claims the
invoice is "settled against the agreement itself" (Phase 1A's exact "not
logical" lie this whole feature exists to retire) even when it has zero
receipts and no legacy declaration.

Same fix shape as `agreement_number` (see ag02_agreement_links) — a
denormalized snapshot column written at match time, readable by ANY caller
who can read the invoice at all, no extra request and no extra permission
gate. Backfilled for every invoice that already carries an agreement_id, from
the agreement's CURRENT type — agreement_type is immutable after creation
(no UPDATE path touches purchase_agreements.agreement_type anywhere in this
codebase), so "current type" and "type at match time" are the same value for
every existing row.

Revision ID: ag05_invoice_agreement_type
Revises: ag04_agreement_receipts
Create Date: 2026-08-11
"""
import sqlalchemy as sa
from alembic import op

revision = "ag05_invoice_agreement_type"
down_revision = "ag04_agreement_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("agreement_type", sa.String(length=20), nullable=True),
    )
    op.execute(
        """
        UPDATE invoices
        SET agreement_type = purchase_agreements.agreement_type
        FROM purchase_agreements
        WHERE invoices.agreement_id = purchase_agreements.id
          AND invoices.agreement_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("invoices", "agreement_type")
