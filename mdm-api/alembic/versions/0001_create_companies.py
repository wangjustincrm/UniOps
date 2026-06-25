"""create companies table

Revision ID: 0001_companies
Revises:
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
import uuid

revision = "0001_companies"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "companies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("code", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("legal_name", sa.String(500), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("tagline", sa.String(500), nullable=True),
        sa.Column("logo_data_url", sa.Text, nullable=True),
        sa.Column("logo_file_name", sa.String(255), nullable=True),
        sa.Column("registration_number", sa.String(100), nullable=True),
        sa.Column("tax_id", sa.String(100), nullable=True),
        sa.Column("incorporated_date", sa.Date, nullable=True),
        sa.Column("jurisdiction", sa.String(255), nullable=True),
        sa.Column("primary_address", sa.Text, nullable=False, server_default=""),
        sa.Column("delivery_address", sa.Text, nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("functional_currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("fiscal_year_start", sa.String(5), nullable=True),
        sa.Column("fiscal_year_end", sa.String(5), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("erp_id", sa.String(100), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("companies")
