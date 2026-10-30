"""ba01: budget_alert_states — which alert level each budget line was emailed at

Revision ID: ba01_budget_alert_states
Revises: agr01_consumed_pretax
Create Date: 2026-10-30

ISS-0146 ③: budget threshold alert emails (app/tasks/budget_alerts.py) go out
once per crossing into the warning / over-budget level; this table remembers
the level last alerted per cost center × account × fiscal year. New, empty.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ba01_budget_alert_states"
down_revision = "agr01_consumed_pretax"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_alert_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cost_center_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("used_pct", sa.Numeric(10, 2), nullable=True),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("cost_center_id", "account_id", "fiscal_year",
                            name="uq_budget_alert_state_line"),
    )


def downgrade() -> None:
    op.drop_table("budget_alert_states")
