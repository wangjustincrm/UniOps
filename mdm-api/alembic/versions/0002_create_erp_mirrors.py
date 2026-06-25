"""create ERP mirror tables

Revision ID: 0002_erp_mirrors
Revises: 0001_companies
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid

revision = "0002_erp_mirrors"
down_revision = "0001_companies"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "erp_materials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("erp_part_no", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("unit_meas", sa.String(20), nullable=True),
        sa.Column("dim_quality", sa.String(100), nullable=True),
        sa.Column("weight_net", sa.Numeric(12, 4), nullable=True),
        sa.Column("weight_gross", sa.Numeric(12, 4), nullable=True),
        sa.Column("volume", sa.Numeric(12, 6), nullable=True),
        sa.Column("part_status", sa.String(10), nullable=True, index=True),
        sa.Column("item_mes_type", sa.String(20), nullable=True, index=True),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("erp_rowversion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "erp_suppliers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("erp_supplier_code", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("supplier_name", sa.String(255), nullable=False),
        sa.Column("supplier_address", sa.Text, nullable=True),
        sa.Column("supplier_tel", sa.String(50), nullable=True),
        sa.Column("supplier_fax", sa.String(50), nullable=True),
        sa.Column("supplier_type", sa.String(20), nullable=True),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("erp_rowversion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "erp_persons",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("erp_person_code", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("person_name", sa.String(255), nullable=False),
        sa.Column("company_code", sa.String(50), nullable=True),
        sa.Column("company_name", sa.String(255), nullable=True),
        sa.Column("department_code", sa.String(50), nullable=True, index=True),
        sa.Column("department_name", sa.String(255), nullable=True),
        sa.Column("is_valid", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("pk_psndoc", sa.String(50), nullable=True),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("erp_rowversion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "erp_sync_state",
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("last_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(20), nullable=True),
        sa.Column("last_message", sa.Text, nullable=True),
        sa.Column("last_row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("erp_sync_state")
    op.drop_table("erp_persons")
    op.drop_table("erp_suppliers")
    op.drop_table("erp_materials")
