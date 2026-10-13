"""The manufacturer's contact, on the chemical and on its label.

Round 6, TC-09: "also the manufacturer's name contact must be shown." The label
carried a supplier name and nothing to reach them by; the chemical had nowhere
to keep it. One free-text line on each — a phone number, an address, or both,
as the data sheet gives it. The label takes the chemical's when it is drafted
and can be corrected before it is approved, like every other field on it.

Revision ID: 20261013_0062
Revises: 20261013_0061
"""
import sqlalchemy as sa
from alembic import op

revision = "20261013_0062"
down_revision = "20261013_0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_chemicals", sa.Column("manufacturer_contact", sa.String(300), nullable=True))
    op.add_column("ehs_chemical_labels", sa.Column("supplier_contact", sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_chemical_labels", "supplier_contact")
    op.drop_column("ehs_chemicals", "manufacturer_contact")
