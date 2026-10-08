"""pa_po_links: a Payment Application can settle several purchase orders.

Revision ID: pa01_pa_po_links
Revises: ag09_agreement_active_months
Create Date: 2026-09-03

Until now a PA carried exactly one PO (`payment_applications.po_id`), so paying
three POs of the same vendor meant three PAs, three approvals and three
remittances. This table is the complete PO set of a PA.

`payment_applications.po_id` / `po_number` are NOT dropped: they keep meaning
the PRIMARY PO (sort_order 0), which is what the finance / approval / expense
mirrors, the NC and QBO exports and every historical PDF already read. The
backfill below puts every existing PO-based PA's single PO into this table, so
from the first moment "the POs of a PA" can be answered here alone — a reader
that consults this table never has to also union in the header column.

Agreement PAs and OA Direct PAs get no rows (they have no PO).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "pa01_pa_po_links"
down_revision = "ag09_agreement_active_months"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pa_po_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("pa_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("po_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("po_number", sa.String(40), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["pa_id"], ["payment_applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["po_id"], ["purchase_orders.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("pa_id", "po_id", name="uq_pa_po_links_pa_po"),
    )
    op.create_index("ix_pa_po_links_pa_id", "pa_po_links", ["pa_id"])
    op.create_index("ix_pa_po_links_po_id", "pa_po_links", ["po_id"])

    # Backfill: one row per existing PO-based PA. po_number is taken from the PO
    # itself rather than the PA's snapshot — a renumbered PO may have left a
    # stale snapshot behind on an old PA, and this table is meant to be the
    # answer to "which PO", not a copy of an old answer.
    op.execute(
        """
        INSERT INTO pa_po_links (id, pa_id, po_id, po_number, sort_order)
        SELECT gen_random_uuid(), pa.id, pa.po_id,
               COALESCE(po.number, pa.po_number, ''), 0
        FROM payment_applications pa
        JOIN purchase_orders po ON po.id = pa.po_id
        WHERE pa.po_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_pa_po_links_po_id", table_name="pa_po_links")
    op.drop_index("ix_pa_po_links_pa_id", table_name="pa_po_links")
    op.drop_table("pa_po_links")
