"""po01: purchase_orders.payment_terms (ISS-0087)

Revision ID: po01_po_payment_terms
Revises: mg01_fr02_ba01
Create Date: 2026-11-02

PRD §3.12.1 lists "Payment terms (defaulted from vendor; overridable)" among
the PO fields; the PO had none. Structure only: a nullable column with the
same codes as vendors.payment_terms (net<N> / cod / prepayment). Existing and
NC-mirrored POs keep NULL, which the API reads as "the vendor's terms" — no
data is rewritten.
"""
import sqlalchemy as sa
from alembic import op

revision = "po01_po_payment_terms"
down_revision = "mg01_fr02_ba01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_orders", sa.Column("payment_terms", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("purchase_orders", "payment_terms")
