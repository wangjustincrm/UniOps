"""chart_of_accounts (IFRS, CA dairy manufacturer) + account_mappings (Phase a A1)

Seed is a STARTING TEMPLATE for finance review — accounts and mappings are
data, maintained via the COA API afterwards. IFRS notes: right-of-use assets
and lease liabilities (IFRS 16) included; GST/HST/QST receivable = ITC.

Revision ID: 0005_coa
Revises: 0004_dims_periods
Create Date: 2026-06-12
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005_coa"
down_revision = "0004_dims_periods"
branch_labels = None
depends_on = None


def upgrade():
    coa = op.create_table(
        "chart_of_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(10), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("account_type", sa.String(20), nullable=False),
        sa.Column("subtype", sa.String(50), nullable=True),
        sa.Column("normal_balance", sa.String(6), nullable=False),
        sa.Column("is_postable", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("parent_code", sa.String(10), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    mappings = op.create_table(
        "account_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("mapping_type", sa.String(20), nullable=False),
        sa.Column("source_code", sa.String(50), nullable=False),
        sa.Column("account_code", sa.String(10), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("mapping_type", "source_code", name="uq_account_mappings_source"),
    )

    def a(code, name, atype, nb, subtype=None, parent=None, postable=True):
        return dict(id=uuid.uuid4(), code=code, name=name, account_type=atype,
                    subtype=subtype, normal_balance=nb, is_postable=postable,
                    parent_code=parent, is_active=True, entity_id=None)

    D, C = "debit", "credit"
    op.bulk_insert(coa, [
        # ── Assets (1xxx) ──────────────────────────────────────────────────
        a("1000", "Cash and Cash Equivalents", "asset", D, "cash", postable=False),
        a("1010", "Bank — CAD Operating", "asset", D, "cash", "1000"),
        a("1020", "Bank — USD", "asset", D, "cash", "1000"),
        a("1100", "Accounts Receivable", "asset", D, "accounts_receivable"),
        a("1150", "Allowance for Doubtful Accounts", "asset", C, "accounts_receivable"),
        a("1200", "Inventory", "asset", D, "inventory", postable=False),
        a("1210", "Inventory — Raw Materials (Milk/Powder)", "asset", D, "inventory", "1200"),
        a("1220", "Inventory — Packaging", "asset", D, "inventory", "1200"),
        a("1230", "Inventory — Work in Process", "asset", D, "inventory", "1200"),
        a("1240", "Inventory — Finished Goods", "asset", D, "inventory", "1200"),
        a("1300", "Prepaid Expenses & Deposits", "asset", D, "prepaid"),
        a("1310", "Prepayments to Vendors", "asset", D, "prepaid"),
        a("1400", "GST/HST Receivable (ITC)", "asset", D, "tax_receivable"),
        a("1410", "QST Receivable (ITR)", "asset", D, "tax_receivable"),
        a("1500", "Property, Plant & Equipment", "asset", D, "ppe", postable=False),
        a("1510", "Production Equipment", "asset", D, "ppe", "1500"),
        a("1520", "Buildings & Improvements", "asset", D, "ppe", "1500"),
        a("1530", "Office Equipment & IT", "asset", D, "ppe", "1500"),
        a("1590", "Accumulated Depreciation", "asset", C, "ppe", "1500"),
        a("1600", "Right-of-Use Assets (IFRS 16)", "asset", D, "rou_asset"),
        a("1700", "Intangible Assets", "asset", D, "intangible"),
        # ── Liabilities (2xxx) ─────────────────────────────────────────────
        a("2000", "Accounts Payable", "liability", C, "accounts_payable"),
        a("2100", "Accrued Liabilities", "liability", C, "accrued"),
        a("2110", "Goods Received Not Invoiced (GRNI)", "liability", C, "accrued"),
        a("2200", "GST/HST Payable", "liability", C, "tax_payable"),
        a("2210", "PST/RST Payable", "liability", C, "tax_payable"),
        a("2220", "QST Payable", "liability", C, "tax_payable"),
        a("2300", "Payroll Liabilities", "liability", C, "payroll"),
        a("2310", "Employee Reimbursements Payable", "liability", C, "payroll"),
        a("2400", "Income Tax Payable", "liability", C, "tax_payable"),
        a("2500", "Lease Liabilities (IFRS 16)", "liability", C, "lease"),
        a("2600", "Long-term Debt", "liability", C, "debt"),
        # ── Equity (3xxx) ──────────────────────────────────────────────────
        a("3000", "Share Capital", "equity", C, "capital"),
        a("3100", "Retained Earnings", "equity", C, "retained"),
        a("3200", "Current Year Earnings", "equity", C, "retained", postable=False),
        # ── Revenue (4xxx) ─────────────────────────────────────────────────
        a("4000", "Sales — Domestic", "revenue", C, "sales"),
        a("4100", "Sales — Export", "revenue", C, "sales"),
        a("4200", "Other Income", "revenue", C, "other_income"),
        a("4900", "Sales Returns & Allowances", "revenue", D, "sales"),
        # ── COGS (5xxx) ────────────────────────────────────────────────────
        a("5000", "COGS — Materials", "expense", D, "cogs"),
        a("5100", "COGS — Direct Labour", "expense", D, "cogs"),
        a("5200", "COGS — Manufacturing Overhead", "expense", D, "cogs"),
        a("5300", "Inventory Adjustments / Scrap / Recall", "expense", D, "cogs"),
        a("5400", "Purchase Price Variance", "expense", D, "variance"),
        a("5410", "Usage / Yield Variance", "expense", D, "variance"),
        a("5500", "Freight In & Landed Costs", "expense", D, "cogs"),
        # ── Operating expenses (6xxx) ──────────────────────────────────────
        a("6000", "Salaries & Benefits", "expense", D, "opex"),
        a("6100", "Utilities", "expense", D, "opex"),
        a("6200", "Repairs & Maintenance", "expense", D, "opex"),
        a("6300", "Laboratory & Quality Testing", "expense", D, "opex"),
        a("6400", "Office & Administration", "expense", D, "opex"),
        a("6500", "Travel & Meals", "expense", D, "opex"),
        a("6600", "Marketing & Trade Promotion", "expense", D, "opex"),
        a("6700", "Insurance & Licenses", "expense", D, "opex"),
        a("6800", "Professional Fees", "expense", D, "opex"),
        a("6900", "Depreciation & Amortization", "expense", D, "opex"),
        a("6950", "Bank Charges & Interest", "expense", D, "opex"),
        a("6990", "FX Gain/Loss", "expense", D, "opex"),
    ])

    def m(mtype, src, acct):
        return dict(id=uuid.uuid4(), mapping_type=mtype, source_code=src,
                    account_code=acct, entity_id=None)

    op.bulk_insert(mappings, [
        # posting line_role → ledger account (payment executor stamps these)
        m("line_role", "accounts_payable", "2000"),
        m("line_role", "bank", "1010"),
        # fallback only — line-level precision comes from budget_account
        # mappings (A2/A5); claims at payment are expensed cash-basis for now
        m("line_role", "employee_expense", "6400"),
        m("line_role", "sales_tax", "1400"),         # ITC
        # budget_account → COA bridge: seeded empty on purpose — finance fills
        # via the mappings API once the budget catalog is reviewed (A1 note)
    ])


def downgrade():
    op.drop_table("account_mappings")
    op.drop_table("chart_of_accounts")
