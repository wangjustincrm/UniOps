"""add nc_purchase_cutover to company_config

Revision ID: nc02_nc_cutover
Revises: nc01_nc_provenance
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "nc02_nc_cutover"
down_revision = "nc01_nc_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_config",
        sa.Column("nc_purchase_cutover", sa.String(length=19), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_config", "nc_purchase_cutover")
