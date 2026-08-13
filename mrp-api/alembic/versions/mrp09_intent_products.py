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
    op.drop_column("mrp_forecast_lines", "intent_name")
    op.drop_column("mrp_forecast_lines", "is_intent")
    op.drop_table("mrp_intent_products")
