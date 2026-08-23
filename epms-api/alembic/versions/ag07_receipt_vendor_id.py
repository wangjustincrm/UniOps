"""Bind an agreement receipt's merchant to the vendor master data.

Task 14 (raised by the user while testing this branch, and the direct sequel
to ag06): ag06 stored the merchant printed on the slip as FREE TEXT, and the
"is this slip on the right house account?" verdict had to be guessed by
comparing that text against the agreement's vendor name. Text comparison is
where `Princess Auto #12`, `7-11` and `7-Eleven` all came from — every one of
those was a limit of comparing spellings, not a limit of the rule. With a real
`vendor_id` on the receipt the same question is an `==` on two ids, and the
fuzzy text rule degrades to what it should always have been: the fallback for
when there is no id.

NULLABLE, and that is the whole decision of this task, not an oversight: a
counter slip routinely comes from a one-off merchant that is not in the vendor
master and has no business being added to it just so a $14 slip can be filed.
Forcing a master-data record first would stop the recorder at the counter. A
receipt with no `vendor_id` keeps its free-text `vendor_name` and is still a
perfectly valid, submittable receipt.

RESTRICT (not CASCADE, not SET NULL) — the same ondelete `invoices.vendor_id`
uses (models/invoice.py). A vendor with receipts recorded against it must not
be deletable out from under them, and a receipt whose vendor silently became
NULL would look, to every reader, exactly like a receipt that was never
matched to master data in the first place.

⚠️ The column definition here must stay byte-for-byte in step with
models/agreement_receipt.py: the test database is built by
Base.metadata.create_all and never runs these migrations, so anything true
only here is true nowhere a test can see it (this branch has already shipped
one such invisible constraint).

Revision ID: ag07_receipt_vendor_id
Revises: ag06_receipt_vendor
Create Date: 2026-08-12
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ag07_receipt_vendor_id"
down_revision = "ag06_receipt_vendor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agreement_receipts",
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # `business_partners` is the physical table behind the Vendor ORM model
    # (see models/vendor.py — `vendors` is a compatibility VIEW since B3, and
    # a VIEW cannot be the target of a foreign key).
    op.create_foreign_key(
        "fk_agr_receipt_vendor_id", "agreement_receipts", "business_partners",
        ["vendor_id"], ["id"], ondelete="RESTRICT",
    )
    # Indexed like every other id column on this table (ix_agr_receipt_* naming
    # follows ag04, which created them all). "Which receipts are on this
    # vendor?" is the natural question once receipts carry a vendor at all.
    op.create_index("ix_agr_receipt_vendor_id", "agreement_receipts", ["vendor_id"])


def downgrade() -> None:
    op.drop_index("ix_agr_receipt_vendor_id", table_name="agreement_receipts")
    op.drop_constraint("fk_agr_receipt_vendor_id", "agreement_receipts", type_="foreignkey")
    op.drop_column("agreement_receipts", "vendor_id")
