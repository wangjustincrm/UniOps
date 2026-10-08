"""add owner_id to purchase_requests — who confirms a service/project receipt

Revision ID: po01_pr_owner_id
Revises: as01_assistant_usage
Create Date: 2026-09-15

The person who RAISES a service PR is not always the person who can say the
service was delivered: an admin raises the requisition on behalf of a line, and
the engineer who supervised the work is the one who knows it finished. Until
now every service-receipt nudge (app/tasks/service_gr_due.py, and the
invoice-driven confirm_receipt in api/v1/invoices.py) went to
PurchaseRequest.created_by, so those reminders landed on someone with no way to
answer them.

owner_id names that person, for procurement types 4 (Service) and 6
(Project-Related) — the same pair that runs the service GR flow and the same
pair that carries service_completion_date.

Deliberately NO backfill, and deliberately nullable: NULL means "the requester",
resolved at read time by app/crud/pr_owner.py. Writing created_by into every
existing row would look like a decision somebody made and would go stale the
moment Data Maintenance reassigns a PR's requester (admin/service.py does
exactly that for PMS-imported documents). The fallback is the invariant; the
column only records a deviation from it.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "po01_pr_owner_id"
down_revision = "as01_assistant_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchase_requests",
        sa.Column("owner_id", UUID(as_uuid=True), nullable=True),
    )
    # RESTRICT, matching purchase_requests.created_by and agreements.owner_id: a
    # user named as a service owner must be reassigned (Data Maintenance) before
    # they can be deleted, never silently detached from the open obligation.
    op.create_foreign_key(
        "fk_purchase_requests_owner_id_users",
        "purchase_requests", "users",
        ["owner_id"], ["id"], ondelete="RESTRICT",
    )
    # The due-date sweep never queries by owner, but "what is still waiting on
    # me" does — access_scope.visible_pr_subquery ORs owner_id = me into every
    # restricted viewer's PR scope.
    op.create_index("ix_purchase_requests_owner_id", "purchase_requests", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_purchase_requests_owner_id", table_name="purchase_requests")
    op.drop_constraint(
        "fk_purchase_requests_owner_id_users", "purchase_requests", type_="foreignkey")
    op.drop_column("purchase_requests", "owner_id")
