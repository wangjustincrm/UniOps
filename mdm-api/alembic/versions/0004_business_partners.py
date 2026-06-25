"""business_partners master + id-preserving copy from vendors (Phase 0-B3)

Creates the table and copies every vendors row (same UUIDs, is_supplier=true).
Does NOT touch FKs or drop vendors — that is epms-api migration
x5_repoint_vendor_fks, which MUST run after this one (it asserts this table
exists). Deployment order: mdm 0004 → epms x5.

Revision ID: 0004_business_partners
Revises: 0003_tax_master
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0004_business_partners"
down_revision = "0003_tax_master"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "business_partners",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("erp_id", sa.String(100), nullable=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("contact_name", sa.String(255), nullable=False),
        sa.Column("contact_email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("payment_terms", sa.String(20), nullable=False, server_default="net30"),
        sa.Column("max_prepayment_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_supplier", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_customer", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("tax_number", sa.String(50), nullable=True),
        sa.Column("customer_type", sa.String(20), nullable=True),
        sa.Column("province", sa.String(2), nullable=True),
        sa.Column("credit_limit", sa.Numeric(15, 2), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # id-preserving copy; tolerate fresh DBs where vendors does not exist
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.vendors') IS NOT NULL THEN
                INSERT INTO business_partners (
                    id, code, erp_id, name, category, contact_name, contact_email,
                    phone, address, payment_terms, max_prepayment_pct, currency,
                    is_active, notes, is_supplier, is_customer, created_at, updated_at
                )
                SELECT id, code, erp_id, name, category, contact_name, contact_email,
                       phone, address, payment_terms, max_prepayment_pct, currency,
                       is_active, notes, true, false, created_at, updated_at
                FROM vendors
                ON CONFLICT (code) DO NOTHING;
            END IF;
        END $$;
    """)


def downgrade():
    op.drop_table("business_partners")
