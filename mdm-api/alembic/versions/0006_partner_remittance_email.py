"""business_partners.remittance_email — where remittance advice is sent.

Revision ID: 0006_partner_remittance_email
Revises: 0005_units_of_measure
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_partner_remittance_email"
down_revision = "0005_units_of_measure"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("business_partners",
                  sa.Column("remittance_email", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("business_partners", "remittance_email")
