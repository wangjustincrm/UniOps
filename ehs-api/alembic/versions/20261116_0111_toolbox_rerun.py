"""A toolbox talk held again (UAT 13).

Kirk: "allow a supervisor the ability to pick a previous toolbox talk and redo
the training session with staff … open up a new 'Who was at the talk' form.
Ensure the data is recorded for tracking purposes." A delivery that repeats an
earlier one names it; the one-per-person rule now holds for first deliveries.

Revision ID: 20261116_0111
Revises: 20261115_0110
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261116_0111"
down_revision = "20261115_0110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_tbt_deliveries", sa.Column(
        "rerun_of", UUID(as_uuid=True),
        sa.ForeignKey("ehs_tbt_deliveries.id", ondelete="SET NULL"), nullable=True))
    op.drop_constraint("uq_ehs_tbt_delivery", "ehs_tbt_deliveries", type_="unique")
    op.create_index("uq_ehs_tbt_delivery_first", "ehs_tbt_deliveries", ["talk_id", "supervisor_id"],
                    unique=True, postgresql_where=sa.text("rerun_of IS NULL"))


def downgrade() -> None:
    op.execute("DELETE FROM ehs_tbt_deliveries WHERE rerun_of IS NOT NULL")
    op.drop_index("uq_ehs_tbt_delivery_first", table_name="ehs_tbt_deliveries")
    op.create_unique_constraint("uq_ehs_tbt_delivery", "ehs_tbt_deliveries", ["talk_id", "supervisor_id"])
    op.drop_column("ehs_tbt_deliveries", "rerun_of")
