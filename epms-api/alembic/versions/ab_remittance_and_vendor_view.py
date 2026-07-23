"""vendors view + company_config.remittance_config.

The `vendors` compatibility view (x5_repoint_vendor_fks) lists its columns
explicitly, so a new business_partners column does NOT appear in it. Any
reader going through that view (known: expense-api's read-only EpmsVendor
mirror) needs it recreated here. EPMS's own Vendor ORM model reads
business_partners directly (not through this view), but is kept in sync too
(see app/models/vendor.py).

Revision ID: ab_remittance_and_vendor_view
Revises: aa_default_match_tolerance_5
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ab_remittance_and_vendor_view"
down_revision = "aa_default_match_tolerance_5"
branch_labels = None
depends_on = None

# Verbatim from x5_repoint_vendor_fks._VIEW_COLS (confirmed unchanged 2026-07-22).
_VIEW_COLS_OLD = (
    "id, code, erp_id, name, category, contact_name, contact_email, phone, "
    "address, payment_terms, max_prepayment_pct, currency, is_active, notes, "
    "created_at, updated_at"
)
_VIEW_COLS_NEW = _VIEW_COLS_OLD + ", remittance_email"


def upgrade():
    # company_config.remittance_config: {enabled, from_email, from_name,
    # cc_email, smtp_user, smtp_password}. JSONB defaulting to {} so every
    # consumer reads through .get() with an explicit fallback (see
    # app/models/config.py for the full rationale).
    op.add_column("company_config", sa.Column(
        "remittance_config", postgresql.JSONB,
        nullable=False, server_default=sa.text("'{}'::jsonb")))

    op.execute("DROP VIEW IF EXISTS vendors")
    op.execute(
        f"CREATE VIEW vendors AS SELECT {_VIEW_COLS_NEW} "
        "FROM business_partners WHERE is_supplier"
    )


def downgrade():
    op.execute("DROP VIEW IF EXISTS vendors")
    op.execute(
        f"CREATE VIEW vendors AS SELECT {_VIEW_COLS_OLD} "
        "FROM business_partners WHERE is_supplier"
    )

    op.drop_column("company_config", "remittance_config")
