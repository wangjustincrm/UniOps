"""Store the merchant printed on an agreement receipt.

Task 13 (raised by the user while testing this branch): the agreement already
carries a `vendor_id`/`vendor_name`, so "which supplier is this house account
with" was always renderable. What was NOT renderable is the merchant printed
on the slip itself — and that is the fact worth keeping, because the classic
house-account mis-posting is a slip from shop A recorded against shop B's
account. Only by storing what the paper says can the two be compared.

Nullable on purpose: OCR returns null whenever the header is illegible or
cropped (`_SLIP_PROMPT` in expense-api treats that as normal, not an error),
and manual entry must not be blocked on a field the recorder may not have.
An empty value is never reported as a mismatch — see
`schemas/agreement_receipt.py::is_vendor_mismatch`.

No index: nothing queries or sorts by it. The mismatch flag is derived per
row at read time from a column that is already selected, not searched for.

Revision ID: ag06_receipt_vendor
Revises: ag05_invoice_agreement_type
Create Date: 2026-08-12
"""
import sqlalchemy as sa
from alembic import op

revision = "ag06_receipt_vendor"
down_revision = "ag05_invoice_agreement_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agreement_receipts",
        sa.Column("vendor_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agreement_receipts", "vendor_name")
