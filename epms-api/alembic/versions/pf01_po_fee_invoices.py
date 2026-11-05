"""pf01: Incoterms code + settings, and fee invoices linked to raw-material POs

Revision ID: pf01_po_fee_invoices
Revises: id02_document_counters
Create Date: 2026-10-23

Design: docs/superpowers/specs/2026-10-23-po-fee-invoices-design.md.

* purchase_orders.incoterms_code — the picked Incoterms code. The existing
  `incoterms` column becomes the named place. Backfill: a stored value whose
  first token is a known code is split ("DAP CRM" -> DAP + "CRM",
  "FOB, Arbuckle, CA" -> FOB + "Arbuckle, CA"); anything else is left as it is
  with no code. Production on 2026-10-23: 7 of ~280 raw-material POs had a value.
* company_config.incoterms_terms — seeded with the eleven Incoterms 2020 codes
  and, from the buyer's side, whether each leaves the main freight / import
  clearance to us. Admins adjust them in Incoterms Settings.
* invoice_fee_links — see app/models/invoice_fee_link.py.
"""
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "pf01_po_fee_invoices"
down_revision = "id02_document_counters"
branch_labels = None
depends_on = None

# Kept in step with app.crud.po_fee.DEFAULT_INCOTERMS (a test pins the two).
_SEED = [
    ("EXW", "Ex Works", True, True),
    ("FCA", "Free Carrier", True, True),
    ("FAS", "Free Alongside Ship", True, True),
    ("FOB", "Free On Board", True, True),
    ("CPT", "Carriage Paid To", False, True),
    ("CIP", "Carriage and Insurance Paid To", False, True),
    ("CFR", "Cost and Freight", False, True),
    ("CIF", "Cost, Insurance and Freight", False, True),
    ("DAP", "Delivered at Place", False, True),
    ("DPU", "Delivered at Place Unloaded", False, True),
    ("DDP", "Delivered Duty Paid", False, False),
]


# Split "CODE place" / "CODE, place": the leading 3-letter token, upper-cased,
# when it is a seeded code; the remainder (minus separators) stays as the place.
# A module constant so tests/test_po_fee.py runs the very same statement.
SPLIT_SQL = r"""
    update purchase_orders
       set incoterms_code = upper(substring(btrim(incoterms) from '^([A-Za-z]{3})\M')),
           incoterms = nullif(btrim(regexp_replace(btrim(incoterms),
                              '^[A-Za-z]{3}\M[\s,;:\-]*', '')), '')
     where incoterms is not null
       and upper(substring(btrim(incoterms) from '^([A-Za-z]{3})\M')) = any(:codes)
"""


def upgrade() -> None:
    op.add_column("purchase_orders", sa.Column("incoterms_code", sa.String(10), nullable=True))
    op.add_column("company_config", sa.Column(
        "incoterms_terms", postgresql.JSONB(astext_type=sa.Text()),
        nullable=False, server_default="[]"))

    seed = [{"code": c, "label": l, "needs_freight": f, "needs_customs": cu, "active": True}
            for c, l, f, cu in _SEED]
    op.execute(sa.text("update company_config set incoterms_terms = cast(:v as jsonb) "
                       "where incoterms_terms = '[]'::jsonb").bindparams(v=json.dumps(seed)))

    op.execute(sa.text(SPLIT_SQL).bindparams(codes=[c for c, *_ in _SEED]))

    op.create_table(
        "invoice_fee_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_line_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fee_type", sa.String(20), nullable=False),
        sa.Column("po_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("gr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("goods_receipts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoice_fee_links_invoice_id", "invoice_fee_links", ["invoice_id"])
    op.create_index("ix_invoice_fee_links_po_id", "invoice_fee_links", ["po_id"])
    op.create_index("ix_invoice_fee_links_gr_id", "invoice_fee_links", ["gr_id"])


def downgrade() -> None:
    op.drop_table("invoice_fee_links")
    # Put the code back in front of the place so no buyer input is lost.
    op.execute("update purchase_orders set incoterms = btrim(incoterms_code || ' ' || coalesce(incoterms, '')) "
               "where incoterms_code is not null")
    op.drop_column("company_config", "incoterms_terms")
    op.drop_column("purchase_orders", "incoterms_code")
