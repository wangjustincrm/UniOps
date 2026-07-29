"""add department_id to purchase_requests + backfill

Revision ID: ac_add_department_id_to_pr
Revises: ab_remittance_and_vendor_view
Create Date: 2026-07-29
"""
import sqlalchemy as sa
from alembic import op

revision = "ac_add_department_id_to_pr"
down_revision = "ab_remittance_and_vendor_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchase_requests",
        sa.Column("department_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_purchase_requests_department_id", "purchase_requests", ["department_id"]
    )
    op.create_foreign_key(
        "fk_purchase_requests_department_id", "purchase_requests", "departments",
        ["department_id"], ["id"], ondelete="RESTRICT",
    )
    # Backfill (idempotent — only NULL rows):
    # 1) rows with a cost center → that cost center's department
    op.execute("""
        UPDATE purchase_requests pr
        SET department_id = cc.department_id
        FROM cost_centers cc
        WHERE pr.cost_center_id = cc.id AND pr.department_id IS NULL
    """)
    # 2) remaining NULL (no cost center) → the creator's department
    op.execute("""
        UPDATE purchase_requests pr
        SET department_id = u.department_id
        FROM users u
        WHERE pr.created_by = u.id AND pr.department_id IS NULL
    """)
    # 3) realign department_name to the resolved department (keep denorm consistent)
    op.execute("""
        UPDATE purchase_requests pr
        SET department_name = d.name
        FROM departments d
        WHERE pr.department_id = d.id
          AND pr.department_name IS DISTINCT FROM d.name
    """)


def downgrade() -> None:
    op.drop_constraint("fk_purchase_requests_department_id", "purchase_requests", type_="foreignkey")
    op.drop_index("ix_purchase_requests_department_id", table_name="purchase_requests")
    op.drop_column("purchase_requests", "department_id")
