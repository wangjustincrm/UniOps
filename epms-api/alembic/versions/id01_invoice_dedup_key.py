"""invoices: one row per vendor + invoice key (PRD INV-004), enforced by the database

Revision ID: id01_invoice_dedup_key
Revises: dp01_direct_pa
Create Date: 2026-10-21

Until now the only duplicate check was the upload form, against the invoices
its user could see; production had nine duplicate pairs (all uploaded by
PO-scoped requesters who could not see AP's copy). crud.invoice_dedup now
checks every write path; this is the database half, which also closes the race
between two simultaneous uploads.

* vendor_invoice_key — generated (STORED) from vendor_invoice_number with
  app.models.invoice.INVOICE_KEY_SQL: letters and digits only, upper-cased,
  leading zeros dropped. Copied here as a literal on purpose: a migration must
  keep meaning what it meant when it ran.
* dedup_exempt — rows that ALREADY collide keep their place: in each
  (vendor_id, key) group the earliest-created row stays the owner, the later
  ones are marked exempt. Nothing is deleted or renamed. The app lookup still
  sees exempt rows, so they keep blocking new uploads of that number.
  Production at 2026-10-21 (read-only dry run, after AP deleted the unpaid
  duplicates): 5 rows — INV-2026-0603 (WTC 000010: two monthly invoices, same
  number), INV-6727 (PDUcables PRE-PAYMENT), INV-7341 / INV-7350 (Digi-Key,
  Mark's "Prepayment Request…"), INV-5875 (0000001 vs 000001). 25 legacy rows
  have an empty key ("-", "0"…) and are outside the index.
* uq_invoices_vendor_invoice_key — UNIQUE (vendor_id, vendor_invoice_key)
  WHERE NOT dedup_exempt AND vendor_invoice_key <> ''.

The exempted refs are printed so the deploy log records them.
"""
from alembic import op
import sqlalchemy as sa

revision = "id01_invoice_dedup_key"
down_revision = "dp01_direct_pa"
branch_labels = None
depends_on = None

KEY = "ltrim(upper(regexp_replace(vendor_invoice_number, '[^[:alnum:]]', '', 'g')), '0')"


def upgrade() -> None:
    op.execute(f"ALTER TABLE invoices ADD COLUMN vendor_invoice_key varchar(100) "
               f"GENERATED ALWAYS AS ({KEY}) STORED")
    op.add_column("invoices", sa.Column("dedup_exempt", sa.Boolean(), nullable=False,
                                        server_default=sa.text("false")))

    bind = op.get_bind()
    exempted = bind.execute(sa.text("""
        WITH ranked AS (
            SELECT id, internal_ref, vendor_name, vendor_invoice_number,
                   row_number() OVER (PARTITION BY vendor_id, vendor_invoice_key
                                      ORDER BY created_at, internal_ref) AS rn
              FROM invoices
             WHERE vendor_invoice_key <> ''
        )
        UPDATE invoices i SET dedup_exempt = true
          FROM ranked r
         WHERE r.id = i.id AND r.rn > 1
        RETURNING i.internal_ref, i.vendor_name, i.vendor_invoice_number
    """)).fetchall()
    for ref, vendor, number in exempted:
        print(f"id01: kept pre-existing duplicate {ref} ({vendor} / {number}) as dedup_exempt")
    print(f"id01: {len(exempted)} invoice(s) marked dedup_exempt")

    op.create_index(
        "uq_invoices_vendor_invoice_key", "invoices", ["vendor_id", "vendor_invoice_key"],
        unique=True, postgresql_where=sa.text("NOT dedup_exempt AND vendor_invoice_key <> ''"))


def downgrade() -> None:
    op.drop_index("uq_invoices_vendor_invoice_key", table_name="invoices")
    op.drop_column("invoices", "dedup_exempt")
    op.drop_column("invoices", "vendor_invoice_key")
