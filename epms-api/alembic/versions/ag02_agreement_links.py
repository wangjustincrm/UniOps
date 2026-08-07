"""link invoices and payment_applications to purchase_agreements

Revision ID: ag02_agreement_links
Revises: ag01_purchase_agreements
Create Date: 2026-08-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ag02_agreement_links"
down_revision = "ag01_purchase_agreements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("agreement_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("invoices", sa.Column("agreement_number", sa.String(40), nullable=True))
    op.add_column("invoices", sa.Column("match_route", sa.String(20), nullable=True))
    op.add_column("invoices", sa.Column("match_route_auto", sa.Boolean(), nullable=False,
                                        server_default="false"))
    op.add_column("invoices", sa.Column("legacy_settlement", sa.Boolean(), nullable=False,
                                        server_default="false"))
    op.add_column("invoices", sa.Column("legacy_settlement_reason", sa.Text(), nullable=True))
    op.create_foreign_key("fk_invoices_agreement_id", "invoices", "purchase_agreements",
                          ["agreement_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_invoices_agreement_id", "invoices", ["agreement_id"])

    op.add_column("payment_applications",
                  sa.Column("agreement_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("payment_applications", sa.Column("agreement_number", sa.String(40), nullable=True))
    op.create_foreign_key("fk_pa_agreement_id", "payment_applications", "purchase_agreements",
                          ["agreement_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_payment_applications_agreement_id", "payment_applications", ["agreement_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_applications_agreement_id", table_name="payment_applications")
    op.drop_constraint("fk_pa_agreement_id", "payment_applications", type_="foreignkey")
    op.drop_column("payment_applications", "agreement_number")
    op.drop_column("payment_applications", "agreement_id")

    op.drop_index("ix_invoices_agreement_id", table_name="invoices")
    op.drop_constraint("fk_invoices_agreement_id", "invoices", type_="foreignkey")
    op.drop_column("invoices", "legacy_settlement_reason")
    op.drop_column("invoices", "legacy_settlement")
    op.drop_column("invoices", "match_route_auto")
    op.drop_column("invoices", "match_route")
    op.drop_column("invoices", "agreement_number")
    op.drop_column("invoices", "agreement_id")
