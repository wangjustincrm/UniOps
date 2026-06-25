"""tax_codes + tax_rules master data with Canadian seed (Phase 0-B2)

Seed rows are CONFIG, not code — rates and treatments are maintained via the
tax admin API afterwards. Rates as of 2026: GST 5%; HST ON 13% / NS 14% /
NB·NL·PE 15%; PST BC 7% / SK 6% / MB RST 7%; QC QST 9.975%.
PST/RST default recoverable=false (not an ITC); GST/HST/QST recoverable=true.

Revision ID: 0003_tax_master
Revises: 0002_erp_mirrors
Create Date: 2026-06-11
"""
import uuid
from datetime import date

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003_tax_master"
down_revision = "0002_erp_mirrors"
branch_labels = None
depends_on = None

_FROM = date(2020, 1, 1)


def upgrade():
    tax_codes = op.create_table(
        "tax_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(20), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("tax_type", sa.String(10), nullable=False),
        sa.Column("province", sa.String(2), nullable=True),
        sa.Column("rate", sa.Numeric(7, 5), nullable=False),
        sa.Column("recoverable", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("effective_to", sa.Date, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", "effective_from", name="uq_tax_codes_code_from"),
    )

    tax_rules = op.create_table(
        "tax_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("priority", sa.Integer, nullable=False, server_default="10"),
        sa.Column("direction", sa.String(10), nullable=False, server_default="any"),
        sa.Column("province", sa.String(2), nullable=True),
        sa.Column("customer_type", sa.String(20), nullable=True),
        sa.Column("item_tax_class", sa.String(20), nullable=True),
        sa.Column("tax_code_list", JSONB, nullable=False, server_default="[]"),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("effective_from", sa.Date, nullable=True),
        sa.Column("effective_to", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    def code(c, name, ttype, prov, rate, recoverable=True, frm=_FROM, to=None):
        return dict(id=uuid.uuid4(), code=c, name=name, tax_type=ttype, province=prov,
                    rate=rate, recoverable=recoverable, effective_from=frm, effective_to=to,
                    active=True)

    op.bulk_insert(tax_codes, [
        code("GST", "GST 5% (federal)", "GST", None, 0.05),
        code("HST_ON", "HST 13% Ontario", "HST", "ON", 0.13),
        # NS cut 15% → 14% effective 2025-04-01: two effective-dated rows
        code("HST_NS", "HST 15% Nova Scotia (to 2025-03-31)", "HST", "NS", 0.15,
             frm=_FROM, to=date(2025, 3, 31)),
        code("HST_NS", "HST 14% Nova Scotia", "HST", "NS", 0.14, frm=date(2025, 4, 1)),
        code("HST_NB", "HST 15% New Brunswick", "HST", "NB", 0.15),
        code("HST_NL", "HST 15% Newfoundland and Labrador", "HST", "NL", 0.15),
        code("HST_PE", "HST 15% Prince Edward Island", "HST", "PE", 0.15),
        code("PST_BC", "PST 7% British Columbia", "PST", "BC", 0.07, recoverable=False),
        code("PST_SK", "PST 6% Saskatchewan", "PST", "SK", 0.06, recoverable=False),
        code("RST_MB", "RST 7% Manitoba", "RST", "MB", 0.07, recoverable=False),
        code("QST", "QST 9.975% Quebec", "QST", "QC", 0.09975),
        code("ZERO", "Zero-rated 0%", "NONE", None, 0.0),
        code("EXEMPT", "Exempt 0% (no ITC)", "NONE", None, 0.0, recoverable=False),
    ])

    def rule(priority, codes, desc, direction="any", prov=None, cust=None, klass=None):
        return dict(id=uuid.uuid4(), priority=priority, direction=direction, province=prov,
                    customer_type=cust, item_tax_class=klass, tax_code_list=codes,
                    description=desc, active=True, effective_from=None, effective_to=None)

    op.bulk_insert(tax_rules, [
        # specific treatments first (priority 100)
        rule(100, ["ZERO"], "Zero-rated items (e.g. basic groceries incl. milk powder)", klass="zero_rated"),
        rule(100, ["EXEMPT"], "Exempt items/services", klass="exempt"),
        rule(100, ["ZERO"], "Exports are zero-rated", cust="export"),
        # provincial standard combos (priority 10)
        rule(10, ["HST_ON"], "Ontario HST", prov="ON"),
        rule(10, ["HST_NS"], "Nova Scotia HST", prov="NS"),
        rule(10, ["HST_NB"], "New Brunswick HST", prov="NB"),
        rule(10, ["HST_NL"], "Newfoundland HST", prov="NL"),
        rule(10, ["HST_PE"], "PEI HST", prov="PE"),
        rule(10, ["GST", "QST"], "Quebec GST+QST", prov="QC"),
        rule(10, ["GST", "PST_BC"], "BC GST+PST", prov="BC"),
        rule(10, ["GST", "PST_SK"], "Saskatchewan GST+PST", prov="SK"),
        rule(10, ["GST", "RST_MB"], "Manitoba GST+RST", prov="MB"),
        # fallback: GST-only (AB, territories, unknown province) — priority 1
        rule(1, ["GST"], "GST-only fallback (AB/YT/NT/NU or unspecified)"),
    ])


def downgrade():
    op.drop_table("tax_rules")
    op.drop_table("tax_codes")
