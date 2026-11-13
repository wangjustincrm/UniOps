"""tz01: company time zone setting

Revision ID: tz01_company_timezone
Revises: fr03_freight_shipment_att
Create Date: 2026-11-11

The plant's time zone becomes a company setting (Portal → Admin → Company
Settings) instead of being hard-coded or taken from the server's UTC clock.
An IANA name, so daylight saving is handled. Every service reads it from this
column (app/core/plant_time.py). Purely additive; the default is the zone the
code assumed until now.
"""
import sqlalchemy as sa
from alembic import op

revision = "tz01_company_timezone"
down_revision = "fr03_freight_shipment_att"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_config",
        sa.Column("timezone", sa.String(64), nullable=False, server_default="America/Toronto"),
    )


def downgrade() -> None:
    op.drop_column("company_config", "timezone")
