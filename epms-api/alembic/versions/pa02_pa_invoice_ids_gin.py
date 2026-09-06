"""GIN index on payment_applications.invoice_ids.

Revision ID: pa02_pa_invoice_ids_gin
Revises: pa01_pa_po_links
Create Date: 2026-09-06

GET /po and GET /po/{id} answer "does this PO have an invoice a new payment
could settle", and the second half of that — is the invoice already claimed by
a live payment application — is a `?|` containment test against this JSONB
array. There is no FK from a PA to its invoices; the array is the only link
there is, so the test cannot be expressed any other way.

Without an index that is a sequential scan of every payment application ever
made, on two endpoints the PO list and the PA create screen both hit
repeatedly. Measured on the dev snapshot before the index: 3.8 ms over 6279
rows, and that row count only grows.

Split out of pa01 rather than added to it: pa01 has already been applied in
production, so alembic would never run an amended copy of it and the index
would silently never exist.
"""
from alembic import op


revision = "pa02_pa_invoice_ids_gin"
down_revision = "pa01_pa_po_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_payment_applications_invoice_ids",
        "payment_applications", ["invoice_ids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_payment_applications_invoice_ids", table_name="payment_applications")
