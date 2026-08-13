"""Planning parameters (key-value) + capacity exceptions (one-off per-week
overrides on top of mrp_capacity_rules).

Two pure-additive tables, deliberately split from the destructive
weekly-bucket migration (mrp10b, a later task in the same
2026-08-12-mrp-weekly-planning plan) so this non-destructive half can land
and be tested on its own before mrp10b rewrites the monthly plan tables.

mrp_planning_params backs app/models/params.py's MrpPlanningParam — see
that module's docstring for why the table is a generic key-value store
rather than dedicated columns.

mrp_capacity_exceptions is created here but has no ORM model yet in this
revision: Task 3 adds app/models/capacity.py's exception model + CRUD and
must not re-create this table.

Revision ID: mrp10a_planning_params
Revises: mrp09_intent_products
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "mrp10a_planning_params"
down_revision = "mrp09_intent_products"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mrp_planning_params",
        sa.Column("key", sa.String(50), primary_key=True),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    # Default row so GET /params always has a week_calendar_mode to return,
    # matching week_calendar.py's WEEK_MODES[0] default.
    op.execute(
        "INSERT INTO mrp_planning_params (key, value) "
        "VALUES ('week_calendar_mode', '\"iso_thursday\"'::jsonb)"
    )

    op.create_table(
        "mrp_capacity_exceptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_ref", sa.String(50)),
        sa.Column("constraint_type", sa.String(20), nullable=False),
        sa.Column("limit_value", sa.Numeric(18, 3), nullable=False),
        sa.Column("uom", sa.String(10)),
        sa.Column("reason", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_unique_constraint(
        "uq_mrp_capacity_exceptions_week_scope_constraint",
        "mrp_capacity_exceptions",
        ["week_start", "scope_type", "scope_ref", "constraint_type"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_mrp_capacity_exceptions_week_scope_constraint",
        "mrp_capacity_exceptions", type_="unique",
    )
    op.drop_table("mrp_capacity_exceptions")
    op.drop_table("mrp_planning_params")
