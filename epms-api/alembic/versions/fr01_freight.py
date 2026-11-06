"""Courier freight payment: carriers, shipments, invoice lines, PA cost split, PA source

Revision ID: fr01_freight
Revises: ob01_pr_ob_mode_applied
Create Date: 2026-10-19

Design: docs/superpowers/specs/2026-10-19-freight-payment-design.md.

payment_applications.source is the discriminator a Freight PA needs: like OA's
retired Direct PA it has neither po_id nor agreement_id, and "both NULL" was
how every service told a Direct PA apart (EPMS hid it, finance paid it as
pa_dir and skipped the EPMS invoice write-back). Backfill from the two FKs:
agreement_id set → agreement, both NULL → direct, otherwise po.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "fr01_freight"
down_revision = "ob01_pr_ob_mode_applied"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_applications", sa.Column(
        "source", sa.String(20), nullable=False, server_default="po"))
    op.execute("""
        UPDATE payment_applications
           SET source = CASE
                 WHEN agreement_id IS NOT NULL THEN 'agreement'
                 WHEN po_id IS NULL THEN 'direct'
                 ELSE 'po' END
    """)
    op.create_index("ix_payment_applications_source", "payment_applications", ["source"])

    op.create_table(
        "freight_carriers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("vendor_id", UUID(as_uuid=True),
                  sa.ForeignKey("business_partners.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("code", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("tracking_pattern", sa.String(200), nullable=True),
        sa.Column("default_cost_center_id", UUID(as_uuid=True),
                  sa.ForeignKey("cost_centers.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "freight_shipments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("carrier_id", UUID(as_uuid=True),
                  sa.ForeignKey("freight_carriers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tracking_no", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False, server_default="outbound"),
        sa.Column("ship_date", sa.Date, nullable=True),
        sa.Column("cost_center_id", UUID(as_uuid=True),
                  sa.ForeignKey("cost_centers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("department_id", UUID(as_uuid=True),
                  sa.ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("attachment_key", sa.String(500), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="registered"),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("carrier_id", "tracking_no", name="uq_freight_shipment_tracking"),
    )
    op.create_index("ix_freight_shipments_carrier_id", "freight_shipments", ["carrier_id"])
    op.create_index("ix_freight_shipments_tracking_no", "freight_shipments", ["tracking_no"])
    op.create_index("ix_freight_shipments_cost_center_id", "freight_shipments", ["cost_center_id"])
    op.create_index("ix_freight_shipments_status", "freight_shipments", ["status"])
    op.create_index("ix_freight_shipments_created_by", "freight_shipments", ["created_by"])

    op.add_column("invoices", sa.Column(
        "freight_carrier_id", UUID(as_uuid=True),
        sa.ForeignKey("freight_carriers.id", ondelete="RESTRICT"), nullable=True))
    op.create_index("ix_invoices_freight_carrier_id", "invoices", ["freight_carrier_id"])

    op.create_table(
        "freight_invoice_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id", UUID(as_uuid=True),
                  sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("charge_type", sa.String(20), nullable=False, server_default="shipment"),
        sa.Column("tracking_no", sa.String(64), nullable=True),
        sa.Column("package_tracking_nos", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("reference", sa.String(200), nullable=True),
        sa.Column("ship_date", sa.Date, nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("shipment_id", UUID(as_uuid=True),
                  sa.ForeignKey("freight_shipments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("linked_line_id", UUID(as_uuid=True),
                  sa.ForeignKey("freight_invoice_lines.id", ondelete="SET NULL"), nullable=True),
        sa.Column("cost_center_id", UUID(as_uuid=True),
                  sa.ForeignKey("cost_centers.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("match_status", sa.String(20), nullable=False, server_default="unmatched"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_freight_invoice_lines_invoice_id", "freight_invoice_lines", ["invoice_id"])
    op.create_index("ix_freight_invoice_lines_tracking_no", "freight_invoice_lines", ["tracking_no"])
    op.create_index("ix_freight_invoice_lines_shipment_id", "freight_invoice_lines", ["shipment_id"])

    op.create_table(
        "pa_cost_allocations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pa_id", UUID(as_uuid=True),
                  sa.ForeignKey("payment_applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_id", UUID(as_uuid=True),
                  sa.ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("cost_center_id", UUID(as_uuid=True),
                  sa.ForeignKey("cost_centers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("department_id", UUID(as_uuid=True),
                  sa.ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("budget_code", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
    )
    op.create_index("ix_pa_cost_allocations_pa_id", "pa_cost_allocations", ["pa_id"])
    op.create_index("ix_pa_cost_allocations_invoice_id", "pa_cost_allocations", ["invoice_id"])


def downgrade() -> None:
    op.drop_table("pa_cost_allocations")
    op.drop_table("freight_invoice_lines")
    op.drop_index("ix_invoices_freight_carrier_id", table_name="invoices")
    op.drop_column("invoices", "freight_carrier_id")
    op.drop_table("freight_shipments")
    op.drop_table("freight_carriers")
    op.drop_index("ix_payment_applications_source", table_name="payment_applications")
    op.drop_column("payment_applications", "source")
