"""sprint2: vendors, projects, purchase_requests, purchase_orders, approval_events, tasks

Revision ID: b1c2d3e4f5a6
Revises: e4f9a2b1c8d7
Create Date: 2026-03-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'e4f9a2b1c8d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── vendors ────────────────────────────────────────────────────────────────
    op.create_table(
        "vendors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("contact_name", sa.String(255), nullable=False),
        sa.Column("contact_email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("payment_terms", sa.String(20), nullable=False, server_default="net30"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_vendors_code", "vendors", ["code"], unique=True)

    # ── projects ───────────────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("budget", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("start_date", sa.Date, nullable=True),
        sa.Column("end_date", sa.Date, nullable=True),
        sa.Column("owner_dept", sa.String(50), nullable=True),
        sa.Column("manager_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_projects_code", "projects", ["code"], unique=True)

    # ── purchase_requests ──────────────────────────────────────────────────────
    op.create_table(
        "purchase_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("type", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("vendor_name", sa.String(255), nullable=True),
        sa.Column("cost_center_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cost_centers.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("budget_code", sa.String(100), nullable=True),
        sa.Column("required_by", sa.Date, nullable=True),
        sa.Column("delivery_address", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_step_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("po_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("po_number", sa.String(30), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_purchase_requests_number", "purchase_requests", ["number"], unique=True)
    op.create_index("ix_purchase_requests_status", "purchase_requests", ["status"])
    op.create_index("ix_purchase_requests_vendor_id", "purchase_requests", ["vendor_id"])
    op.create_index("ix_purchase_requests_cost_center_id", "purchase_requests", ["cost_center_id"])
    op.create_index("ix_purchase_requests_created_by", "purchase_requests", ["created_by"])

    # ── pr_line_items ──────────────────────────────────────────────────────────
    op.create_table(
        "pr_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("pr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("material_id", sa.String(50), nullable=True),
        sa.Column("supplier_item_id", sa.String(100), nullable=True),
        sa.Column("qty", sa.Numeric(15, 4), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("unit_price", sa.Numeric(15, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(15, 2), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_pr_line_items_pr_id", "pr_line_items", ["pr_id"])

    # ── purchase_orders ────────────────────────────────────────────────────────
    op.create_table(
        "purchase_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("type", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("subtotal", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("vendor_name", sa.String(255), nullable=False),
        sa.Column("budget_code", sa.String(100), nullable=True),
        sa.Column("expected_delivery", sa.Date, nullable=True),
        sa.Column("delivery_address", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("approval_step_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_requests.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("pr_number", sa.String(30), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_purchase_orders_number", "purchase_orders", ["number"], unique=True)
    op.create_index("ix_purchase_orders_status", "purchase_orders", ["status"])
    op.create_index("ix_purchase_orders_vendor_id", "purchase_orders", ["vendor_id"])
    op.create_index("ix_purchase_orders_pr_id", "purchase_orders", ["pr_id"])
    op.create_index("ix_purchase_orders_created_by", "purchase_orders", ["created_by"])

    # ── po_line_items ──────────────────────────────────────────────────────────
    op.create_table(
        "po_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("po_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pr_line_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("pr_line_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("material_id", sa.String(50), nullable=True),
        sa.Column("supplier_item_id", sa.String(100), nullable=True),
        sa.Column("qty", sa.Numeric(15, 4), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("unit_price", sa.Numeric(15, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(15, 2), nullable=False),
        sa.Column("received_qty", sa.Numeric(15, 4), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_po_line_items_po_id", "po_line_items", ["po_id"])

    # ── approval_events ────────────────────────────────────────────────────────
    op.create_table(
        "approval_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("document_type", sa.String(10), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_number", sa.String(40), nullable=False),
        sa.Column("step_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_role", sa.String(50), nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_approval_events_document_type", "approval_events", ["document_type"])
    op.create_index("ix_approval_events_document_id", "approval_events", ["document_id"])

    # ── tasks ──────────────────────────────────────────────────────────────────
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False, server_default="normal"),
        sa.Column("document_type", sa.String(10), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_number", sa.String(40), nullable=False),
        sa.Column("assigned_role", sa.String(50), nullable=False),
        sa.Column("assigned_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("vendor", sa.String(255), nullable=True),
        sa.Column("is_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tasks_type", "tasks", ["type"])
    op.create_index("ix_tasks_document_type", "tasks", ["document_type"])
    op.create_index("ix_tasks_document_id", "tasks", ["document_id"])
    op.create_index("ix_tasks_assigned_role", "tasks", ["assigned_role"])
    op.create_index("ix_tasks_assigned_user_id", "tasks", ["assigned_user_id"])
    op.create_index("ix_tasks_is_completed", "tasks", ["is_completed"])


def downgrade() -> None:
    op.drop_table("tasks")
    op.drop_table("approval_events")
    op.drop_table("po_line_items")
    op.drop_table("purchase_orders")
    op.drop_table("pr_line_items")
    op.drop_table("purchase_requests")
    op.drop_table("projects")
    op.drop_table("vendors")
