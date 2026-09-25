"""Record finance's verdict on an invoice number NC has on more than one payable.

NC does not check that an invoice number is unique on its payables, and it has
been paid twice because of it. Measured on production 2026-09-25: 36 groups
where the same supplier, invoice number and amount sit on two or more approved
payables with no equal credit cancelling the extra copy — 145,053.61 of
duplicate billing, the largest two (Jane Media 1497, 50,722.88 CAD; and
FrieslandCampina 9007737328, 45,800.00 USD) settled twice.

Detection is read off the mirror on every AP sync (see
app/services/ap_duplicate_invoices.py) and never stored: it has to follow NC
the moment a copy is voided or credited. What NC has nowhere to keep is the
JUDGEMENT — "these two are different invoices that happen to share a number",
"the overpayment was recovered" — so that, and only that, lives here.

  * Keyed by the finding's key, not by a mirror row id: full reloads rebuild
    the mirror and its UUIDs do not survive.
  * `bill_nos` is the set of NC bills the reviewer SAW. A review covers a
    finding only while the finding's bills are a subset of it, so a third copy
    entered after the review re-opens the group rather than inheriting a
    verdict nobody gave it.
  * Retired, never deleted (`retired_at`), for the same reason 0035 keeps its
    restores: who cleared a duplicate, when and why must outlive someone
    changing their mind. The unique index is partial on that column.

Revision ID: 0039_ap_inv_dup_reviews
Revises: 0038_jv_nc_header
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0039_ap_inv_dup_reviews"
down_revision = "0038_jv_nc_header"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nc_ap_invoice_dup_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("finding_key", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("invoice_no_norm", sa.String(100), nullable=False),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("supplier_code", sa.String(50), nullable=True),
        sa.Column("bill_nos", ARRAY(sa.String(40)), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_by_name", sa.String(200), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_by", UUID(as_uuid=True), nullable=True),
        sa.Column("retired_by_name", sa.String(200), nullable=True),
    )
    op.create_index("ux_nc_ap_dup_review_live", "nc_ap_invoice_dup_reviews",
                    ["finding_key"], unique=True,
                    postgresql_where=sa.text("retired_at is null"))
    op.create_index("ix_nc_ap_dup_review_invoice", "nc_ap_invoice_dup_reviews",
                    ["invoice_no_norm"])


def downgrade() -> None:
    op.drop_index("ix_nc_ap_dup_review_invoice", table_name="nc_ap_invoice_dup_reviews")
    op.drop_index("ux_nc_ap_dup_review_live", table_name="nc_ap_invoice_dup_reviews")
    op.drop_table("nc_ap_invoice_dup_reviews")
