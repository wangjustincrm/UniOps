"""add service/project expected completion date to purchase_requests

Revision ID: ai01_pr_service_completion
Revises: nc03_po_buyer_details
Create Date: 2026-08-21

PrCreatePage has rendered a "Service Expected Completion Date" field (with a
required asterisk) since the PR form was written, but the value never left the
browser: neither submit payload carried it, no schema declared it, and no
migration ever created the column. Every service PR in the database therefore
has no completion date at all — which is why the PRD's GR-S-001 reminder
("a task is created when the PO's expected completion date is reached") was
never implementable.

This column is the missing data source. It stays nullable because every
pre-existing row must remain valid; the required-ness is enforced at submit
time for procurement types 4 (Service) and 6 (Project-Related) only, which is
the same pair app/api/v1/gr.py treats as the service GR flow.
"""
import sqlalchemy as sa
from alembic import op

revision = "ai01_pr_service_completion"
down_revision = "nc03_po_buyer_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchase_requests",
        sa.Column("service_completion_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("purchase_requests", "service_completion_date")
