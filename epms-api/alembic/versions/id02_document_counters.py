"""document_counters: invoice references are never issued twice

Revision ID: id02_document_counters
Revises: id01_invoice_dedup_key
Create Date: 2026-10-21

Invoice references were MAX(existing) + 1, so deleting the newest invoice
handed its number to the next upload: INV-2026-0653 went to two invoices on
2026-10-13, INV-2026-0618 to three on 2026-10-07/08. Finance keeps the number
on the AP row, and a deleted invoice's orphaned payable then read as the live
invoice's. crud._numbering.next_number_never_reused keeps the highest value
issued per prefix here.

Seeded for every INV-YYYY- prefix with the highest number found in any of:
the invoices themselves, Data Maintenance's delete log (admin_audit_log,
entity invoice), and Finance's AP references for EPMS invoices — so numbers
deleted before today stay retired as well. (Deletes through the Invoices page
were not logged; Finance's references cover those that were ever synced.)
"""
from alembic import op
import sqlalchemy as sa

revision = "id02_document_counters"
down_revision = "id01_invoice_dedup_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_counters",
        sa.Column("prefix", sa.String(60), primary_key=True),
        sa.Column("last_value", sa.Integer(), nullable=False),
    )
    bind = op.get_bind()
    sources = ["SELECT internal_ref AS ref FROM invoices"]
    if bind.execute(sa.text("SELECT to_regclass('admin_audit_log')")).scalar() is not None:
        sources.append("SELECT record_number FROM admin_audit_log WHERE entity = 'invoice'")
    if bind.execute(sa.text("SELECT to_regclass('ap_invoices')")).scalar() is not None:
        sources.append("SELECT source_ref FROM ap_invoices WHERE source = 'epms'")
    rows = bind.execute(sa.text(f"""
        INSERT INTO document_counters (prefix, last_value)
        SELECT substring(ref FROM '^(INV-[0-9]{{4}}-)'),
               max(substring(ref FROM '^INV-[0-9]{{4}}-([0-9]+)$')::int)
          FROM ({' UNION ALL '.join(sources)}) refs
         WHERE ref ~ '^INV-[0-9]{{4}}-[0-9]+$'
         GROUP BY 1
        RETURNING prefix, last_value
    """)).fetchall()
    for prefix, last in rows:
        print(f"id02: {prefix} continues after {last}")


def downgrade() -> None:
    op.drop_table("document_counters")
