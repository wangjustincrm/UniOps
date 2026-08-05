"""Sales forecast versions/lines (Task 1) + consignment stock table (Task 3).

The consignment DDL is merged into this migration per the phase plan
(docs/superpowers/plans/2026-08-04-mrp-phase1a-forecast-inventory-bom.md,
"并入 Task 1 的 mrp02 迁移") — only the table is created here. The
`ForecastVersion`/`ForecastLine` ORM models AND all forecast API endpoints
are this task's scope; the consignment stock ORM model, service, and API
endpoints are Task 3's, added on top of this pre-existing table in a later
commit (no further migration needed for that table).

Revision ID: mrp02_forecast_consignment
Revises: mrp01
Create Date: 2026-08-04
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "mrp02_forecast_consignment"
down_revision = "mrp01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mrp_forecast_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("version_no", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("horizon_start_month", sa.CHAR(7), nullable=False),
        sa.Column("horizon_months", sa.Integer, nullable=False, server_default="18"),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "mrp_forecast_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "version_id", UUID(as_uuid=True),
            sa.ForeignKey("mrp_forecast_versions.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("material_code", sa.String(50), nullable=False, index=True),
        sa.Column("month", sa.CHAR(7), nullable=False),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("uom", sa.String(10), nullable=False, server_default="KG"),
        sa.Column("freeze_flag", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "version_id", "material_code", "month",
            name="uq_mrp_forecast_lines_version_material_month",
        ),
    )

    # Task 3's table (plan doc line 166) — model class + CRUD/API land in
    # Task 3's own commit, not this one.
    op.create_table(
        "mrp_consignment_stock",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("warehouse_code", sa.String(50), nullable=False, server_default="MAIN"),
        sa.Column("material_code", sa.String(50), nullable=False, index=True),
        sa.Column("lot_no", sa.String(50), nullable=False),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("count_date", sa.Date, nullable=False),
        sa.Column("expiry_date", sa.Date, nullable=True),
        sa.Column("expiry_source", sa.String(20), nullable=True),  # 'wms'|'manual'|null
        sa.Column("entered_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "warehouse_code", "material_code", "lot_no", "count_date",
            name="uq_mrp_consignment_stock_wh_mat_lot_date",
        ),
    )


def downgrade() -> None:
    op.drop_table("mrp_consignment_stock")
    op.drop_table("mrp_forecast_lines")
    op.drop_table("mrp_forecast_versions")
