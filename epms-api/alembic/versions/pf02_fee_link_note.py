"""pf02: a note on fee links — what an "other" fee is

Revision ID: pf02_fee_link_note
Revises: pf01_po_fee_invoices
Create Date: 2026-10-26

A fee linked as type "other" says nothing about what it was (inspection,
storage, demurrage…), so it carries a required note (crud.po_fee). Freight
and customs links leave it empty.
"""
import sqlalchemy as sa
from alembic import op

revision = "pf02_fee_link_note"
down_revision = "pf01_po_fee_invoices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice_fee_links", sa.Column("note", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("invoice_fee_links", "note")
