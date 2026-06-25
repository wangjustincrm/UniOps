"""sprint3: goods_receipts, invoices, payment_applications

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-03-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── goods_receipts ─────────────────────────────────────────────────────────
    op.create_table(
        "goods_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("po_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("po_number", sa.String(40), nullable=False),
        sa.Column("pr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_requests.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("pr_number", sa.String(30), nullable=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("vendor_name", sa.String(255), nullable=False),
        sa.Column("gr_type", sa.String(20), nullable=False),
        sa.Column("procurement_type", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending_ack"),
        sa.Column("storage_location", sa.String(255), nullable=True),
        sa.Column("received_by", sa.String(255), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(255), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_by", sa.String(255), nullable=True),
        sa.Column("collection_notes", sa.Text, nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_goods_receipts_number", "goods_receipts", ["number"], unique=True)
    op.create_index("ix_goods_receipts_status", "goods_receipts", ["status"])
    op.create_index("ix_goods_receipts_po_id", "goods_receipts", ["po_id"])
    op.create_index("ix_goods_receipts_vendor_id", "goods_receipts", ["vendor_id"])
    op.create_index("ix_goods_receipts_created_by", "goods_receipts", ["created_by"])

    # ── gr_line_items ──────────────────────────────────────────────────────────
    op.create_table(
        "gr_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("gr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("goods_receipts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("po_line_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("po_line_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("material_id", sa.String(50), nullable=True),
        sa.Column("qty_ordered", sa.Numeric(15, 4), nullable=False),
        sa.Column("qty_received", sa.Numeric(15, 4), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("unit_price", sa.Numeric(15, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(15, 2), nullable=False),
        sa.Column("condition", sa.String(20), nullable=False, server_default="good"),
        sa.Column("discrepancy_notes", sa.Text, nullable=True),
        sa.Column("actual_qty", sa.Numeric(15, 4), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_gr_line_items_gr_id", "gr_line_items", ["gr_id"])

    # ── invoices ───────────────────────────────────────────────────────────────
    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("internal_ref", sa.String(30), nullable=False),
        sa.Column("vendor_invoice_number", sa.String(100), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("vendor_name", sa.String(255), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("invoice_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="unmatched"),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("file_size", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("po_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("po_number", sa.String(40), nullable=True),
        sa.Column("gr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("goods_receipts.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("gr_number", sa.String(30), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("matched_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("po_total", sa.Numeric(15, 2), nullable=True),
        sa.Column("gr_value", sa.Numeric(15, 2), nullable=True),
        sa.Column("variance", sa.Numeric(15, 2), nullable=True),
        sa.Column("variance_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("exception_reason", sa.Text, nullable=True),
        sa.Column("exception_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exception_resolved_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("exception_resolution", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoices_internal_ref", "invoices", ["internal_ref"], unique=True)
    op.create_index("ix_invoices_status", "invoices", ["status"])
    op.create_index("ix_invoices_vendor_id", "invoices", ["vendor_id"])
    op.create_index("ix_invoices_po_id", "invoices", ["po_id"])
    op.create_index("ix_invoices_gr_id", "invoices", ["gr_id"])

    # ── payment_applications ───────────────────────────────────────────────────
    op.create_table(
        "payment_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("pa_number", sa.String(30), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("po_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("po_number", sa.String(40), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("vendor_name", sa.String(255), nullable=False),
        sa.Column("invoice_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("gr_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("pa_type", sa.String(20), nullable=False, server_default="regular"),
        sa.Column("subtotal", sa.Numeric(15, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("shipping_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("other_charges", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("other_charges_note", sa.String(255), nullable=True),
        sa.Column("payment_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prepayment_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("expected_settlement_date", sa.Date, nullable=True),
        sa.Column("settlement_status", sa.String(20), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("settlement_note", sa.Text, nullable=True),
        sa.Column("settlement_variance", sa.Numeric(15, 2), nullable=True),
        sa.Column("approval_step_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_payment_applications_pa_number", "payment_applications", ["pa_number"], unique=True)
    op.create_index("ix_payment_applications_status", "payment_applications", ["status"])
    op.create_index("ix_payment_applications_po_id", "payment_applications", ["po_id"])
    op.create_index("ix_payment_applications_vendor_id", "payment_applications", ["vendor_id"])
    op.create_index("ix_payment_applications_created_by", "payment_applications", ["created_by"])

    # ── pa_line_items ──────────────────────────────────────────────────────────
    op.create_table(
        "pa_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("pa_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("payment_applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("po_line_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("po_line_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("qty", sa.Numeric(15, 4), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("unit_price", sa.Numeric(15, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(15, 2), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_pa_line_items_pa_id", "pa_line_items", ["pa_id"])


def downgrade() -> None:
    op.drop_table("pa_line_items")
    op.drop_table("payment_applications")
    op.drop_table("invoices")
    op.drop_table("gr_line_items")
    op.drop_table("goods_receipts")
