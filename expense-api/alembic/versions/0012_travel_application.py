"""travel application (TRA): traveler roster, transport modes, leave dates, TRV ref

Revision ID: 0012_travel_application
Revises: 0011_invoice_line_unit
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0012_travel_application"
down_revision = "0011_invoice_line_unit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("expense_claims", sa.Column(
        "transport_modes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("expense_claims", sa.Column("leave_from_date", sa.Date(), nullable=True))
    op.add_column("expense_claims", sa.Column("leave_to_date", sa.Date(), nullable=True))
    op.add_column("expense_claims", sa.Column(
        "travel_application_id", UUID(as_uuid=True),
        sa.ForeignKey("expense_claims.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_expense_claims_travel_application_id",
                    "expense_claims", ["travel_application_id"])
    op.create_table(
        "expense_travelers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("claim_id", UUID(as_uuid=True),
                  sa.ForeignKey("expense_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_expense_travelers_claim_id", "expense_travelers", ["claim_id"])
    op.create_index("ix_expense_travelers_user_id", "expense_travelers", ["user_id"])


def downgrade() -> None:
    op.drop_table("expense_travelers")
    op.drop_index("ix_expense_claims_travel_application_id", "expense_claims")
    op.drop_column("expense_claims", "travel_application_id")
    op.drop_column("expense_claims", "leave_to_date")
    op.drop_column("expense_claims", "leave_from_date")
    op.drop_column("expense_claims", "transport_modes")
