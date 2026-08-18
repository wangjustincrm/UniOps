"""Purchase suggestion runs and lines (Phase 1C).

One run per calculation, so a suggestion can be reopened, compared and
audited the same way a production plan can -- purchasing acts on these
numbers, and "what did it say last week" is a question that gets asked.

`status` on a line is the placeholder for the next round's PR hand-off
(pending | ordered | ignored). It exists now so that wiring purchase
requisitions later needs no data migration.

Revision chain: mrp12_plan_versioning -> mrp13_purchase_suggestions.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "mrp13_purchase_suggestions"
down_revision = "mrp12_plan_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mrp_purchase_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_no", sa.String(50), nullable=False, unique=True, index=True),
        # The production plan these suggestions were computed from. Kept so a
        # planner can answer "which plan told us to buy this".
        sa.Column("source_plan_run_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("generated_by", UUID(as_uuid=True), nullable=True),
        # Loss rates as they stood at generate time: changing a setting later
        # must not silently restate what an old suggestion was based on.
        sa.Column("raw_material_loss_rate", sa.Numeric(6, 4), nullable=False,
                  server_default="0"),
        sa.Column("packaging_loss_rate", sa.Numeric(6, 4), nullable=False,
                  server_default="0"),
        sa.Column("stats", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    op.create_table(
        "mrp_purchase_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", UUID(as_uuid=True),
                  sa.ForeignKey("mrp_purchase_runs.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("material_code", sa.String(50), nullable=False, index=True),
        sa.Column("need_week", sa.Date(), nullable=False, index=True),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("gross_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("available_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("net_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("suggested_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("raised_to_moq", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("partner_code", sa.String(50), nullable=True),
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
        # Everything the engine could not establish, kept per line so the
        # screen can say which numbers are load-bearing.
        sa.Column("supplier_missing", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("lead_time_missing", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("order_date_passed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("mrp_purchase_lines")
    op.drop_table("mrp_purchase_runs")
