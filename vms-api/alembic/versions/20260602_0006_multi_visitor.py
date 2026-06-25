"""Add additional_visitor_ids column for multi-visitor visits.

A single Visit appointment can now carry multiple visitors. The original
`visitor_id` stays as the "primary" visitor (badge holder #1, the one
referenced by reports/search by default); additional companions are
stored as a JSONB array of UUIDs.

We chose JSONB over a proper M2M join table to keep the change surface
small — every existing query against vms_visits keeps working unchanged,
and the additional-list only matters in badge printing + report row
expansion. If we later need to join companions in SQL queries, migrate
to vms_visit_visitors then.

Revision ID: 20260602_0006
Revises: 20260602_0005
Create Date: 2026-06-02
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260602_0006"
down_revision = "20260602_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vms_visits",
        sa.Column(
            "additional_visitor_ids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("vms_visits", "additional_visitor_ids")
