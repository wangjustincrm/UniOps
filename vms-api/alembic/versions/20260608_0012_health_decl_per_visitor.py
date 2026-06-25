"""Per-visitor health declarations.

Health declarations were one-per-visit, so a multi-visitor appointment could
only capture a single declaration (the second visitor overwrote the first).
Each person must file their own, so we add `visitor_id` and a unique
(visit_id, visitor_id) constraint.

Existing rows are backfilled to the visit's primary visitor before the column
is made NOT NULL.

Revision ID: 20260608_0012
Revises: 20260606_0011
Create Date: 2026-06-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260608_0012"
down_revision = "20260606_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add nullable so existing rows survive the add.
    op.add_column(
        "vms_health_declarations",
        sa.Column("visitor_id", UUID(as_uuid=True), nullable=True),
    )
    # 2. Backfill each existing declaration to its visit's primary visitor.
    op.execute(
        """
        UPDATE vms_health_declarations h
        SET visitor_id = v.visitor_id
        FROM vms_visits v
        WHERE v.id = h.visit_id AND h.visitor_id IS NULL
        """
    )
    # 3. Now enforce NOT NULL + the FK + uniqueness + index.
    op.alter_column("vms_health_declarations", "visitor_id", nullable=False)
    op.create_foreign_key(
        "fk_health_decl_visitor",
        "vms_health_declarations",
        "vms_visitors",
        ["visitor_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_vms_health_declarations_visitor_id",
        "vms_health_declarations",
        ["visitor_id"],
    )
    op.create_unique_constraint(
        "uq_health_decl_visit_visitor",
        "vms_health_declarations",
        ["visit_id", "visitor_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_health_decl_visit_visitor", "vms_health_declarations", type_="unique")
    op.drop_index("ix_vms_health_declarations_visitor_id", table_name="vms_health_declarations")
    op.drop_constraint("fk_health_decl_visitor", "vms_health_declarations", type_="foreignkey")
    op.drop_column("vms_health_declarations", "visitor_id")
