"""unique (vendor_id, vendor_invoice_number) on ap_invoices — NC reconciliation anchor

Revision ID: 0022_ap_vendor_invno_unique
Revises: 0021_nc_export_batches
Create Date: 2026-07-14
"""
from alembic import op

revision = "0022_ap_vendor_invno_unique"
down_revision = "0021_nc_export_batches"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "create unique index uq_ap_invoices_vendor_invno on ap_invoices "
        "(vendor_id, vendor_invoice_number) "
        "where vendor_id is not null and vendor_invoice_number is not null "
        "and status != 'void'")


def downgrade():
    op.execute("drop index uq_ap_invoices_vendor_invno")
