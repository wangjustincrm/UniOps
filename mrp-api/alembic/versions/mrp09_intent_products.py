"""intent products

Revision ID: mrp09_intent_products
Revises: mrp08_mps_lead_time
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "mrp09_intent_products"
down_revision = "mrp08_mps_lead_time"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mrp_intent_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("bound_material_code", sa.String(50)),
        sa.Column("bound_at", sa.DateTime(timezone=True)),
        sa.Column("bound_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.add_column("mrp_forecast_lines",
                  sa.Column("is_intent", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("mrp_forecast_lines", sa.Column("intent_name", sa.String(200)))


def downgrade() -> None:
    """DESTRUCTIVE — a structural inverse of upgrade(), but NOT a data-safe one.

    Running this drops the entire `mrp_intent_products` registry (every intent
    product ever created, along with its binding history) and both intent
    columns on every `mrp_forecast_lines` row of every historical snapshot.
    None of that is recoverable by re-running `upgrade()`: the table comes
    back empty and `is_intent` comes back `false` for every existing line,
    because `server_default="false"` is what backfills the re-added column.

    The non-obvious consequence of that reset, and the reason this warning is
    here rather than in a review comment: MPS decides what to skip purely
    from the frozen `is_intent` column and deliberately never re-derives it
    from the `INTENT-` code prefix (see `_load_intent_lines` in
    `app/api/v1/mps.py`, whose docstring explains why). So after a
    downgrade→upgrade cycle, every pre-existing snapshot's intent rows look
    like ordinary materials to MPS, and a run off one of those snapshots
    would schedule `INTENT-xxxxxxxx` placeholders as if they were real
    material codes — silently, with no error anywhere.

    Downgrading past this revision on any database that holds real snapshots
    therefore needs those snapshots regenerated (or `is_intent` restored from
    a backup) before MPS is run again.
    """
    op.drop_column("mrp_forecast_lines", "intent_name")
    op.drop_column("mrp_forecast_lines", "is_intent")
    op.drop_table("mrp_intent_products")
