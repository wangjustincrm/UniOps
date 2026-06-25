"""repoint document FKs vendors -> business_partners; vendors becomes a VIEW

⚠️ ORDERING: mdm-api migration 0004_business_partners MUST run first (it
creates business_partners and copies all vendors rows id-preserving).
This migration asserts that and fails loudly otherwise.

Affected FKs (verified against the live DB): purchase_requests,
purchase_orders, goods_receipts, invoices, payment_applications — all
RESTRICT, all keep RESTRICT.

The old `vendors` name lives on as a read-only compatibility VIEW
(is_supplier rows, original column set) for any remaining readers
(known: expense-api's read-only EpmsVendor mirror).

Revision ID: x5_repoint_vendor_fks
Revises: x4_invoice_tax_lines
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa

revision = "x5_repoint_vendor_fks"
down_revision = "x4_invoice_tax_lines"
branch_labels = None
depends_on = None

_FKS = [
    ("purchase_requests", "purchase_requests_vendor_id_fkey"),
    ("purchase_orders", "purchase_orders_vendor_id_fkey"),
    ("goods_receipts", "goods_receipts_vendor_id_fkey"),
    ("invoices", "invoices_vendor_id_fkey"),
    ("payment_applications", "payment_applications_vendor_id_fkey"),
]

_VIEW_COLS = (
    "id, code, erp_id, name, category, contact_name, contact_email, phone, "
    "address, payment_terms, max_prepayment_pct, currency, is_active, notes, "
    "created_at, updated_at"
)


def upgrade():
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT to_regclass('public.business_partners')")).scalar() is None:
        raise RuntimeError(
            "business_partners does not exist — run mdm-api migration "
            "0004_business_partners BEFORE epms-api x5_repoint_vendor_fks"
        )
    # safety: every referenced vendor id must already exist in business_partners
    orphans = conn.execute(sa.text(
        "SELECT count(*) FROM vendors v "
        "WHERE NOT EXISTS (SELECT 1 FROM business_partners bp WHERE bp.id = v.id)"
    )).scalar()
    if orphans:
        raise RuntimeError(
            f"{orphans} vendors rows missing from business_partners — "
            "re-run mdm 0004 copy before repointing FKs"
        )

    for table, fk in _FKS:
        op.drop_constraint(fk, table, type_="foreignkey")
        op.create_foreign_key(
            fk.replace("_fkey", "_bp_fkey"), table, "business_partners",
            ["vendor_id"], ["id"], ondelete="RESTRICT",
        )

    op.execute("DROP TABLE vendors")
    op.execute(
        f"CREATE VIEW vendors AS SELECT {_VIEW_COLS} "
        "FROM business_partners WHERE is_supplier"
    )


def downgrade():
    op.execute("DROP VIEW vendors")
    op.execute(
        f"CREATE TABLE vendors AS SELECT {_VIEW_COLS} "
        "FROM business_partners WHERE is_supplier"
    )
    op.execute("ALTER TABLE vendors ADD PRIMARY KEY (id)")
    for table, fk in _FKS:
        op.drop_constraint(fk.replace("_fkey", "_bp_fkey"), table, type_="foreignkey")
        op.create_foreign_key(fk, table, "vendors", ["vendor_id"], ["id"], ondelete="RESTRICT")
