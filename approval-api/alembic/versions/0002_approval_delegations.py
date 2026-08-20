"""dated approval delegation (代班)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_approval_delegations"
down_revision = "0001_approval_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # btree_gist is required for an EXCLUDE constraint mixing = and &&.
    # It is trusted in PG 13+, so the database owner can create it, and it is
    # already present in production (booking-api's no_double_booking created
    # it and deliberately never drops it).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "approval_delegations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("delegator_user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("delegate_user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("delegator_user_id <> delegate_user_id",
                           name="ck_delegation_not_self"),
        sa.CheckConstraint("end_date >= start_date", name="ck_delegation_date_order"),
    )
    op.create_index("ix_approval_delegations_delegator",
                    "approval_delegations", ["delegator_user_id"])
    op.create_index("ix_approval_delegations_delegate",
                    "approval_delegations", ["delegate_user_id"])
    op.execute(
        "ALTER TABLE approval_delegations ADD CONSTRAINT ex_delegation_no_overlap "
        "EXCLUDE USING gist ("
        "  delegator_user_id WITH =,"
        "  daterange(start_date, end_date, '[]') WITH &&"
        ") WHERE (revoked_at IS NULL)"
    )


def downgrade() -> None:
    op.drop_table("approval_delegations")
    # btree_gist is intentionally NOT dropped — booking-api relies on it.
