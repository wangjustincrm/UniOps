"""The product batch code on an incident.

Round 12, INC-04 and D3: "add a text field for 'product batch code' just under
the 'who investigated' field", and on the report "fit in the batch code text
field on the same line" as the date and time. One column on the incident, shown
in both places.

Revision ID: 20261109_0090
Revises: 20261109_0089
"""
import sqlalchemy as sa
from alembic import op

revision = "20261109_0090"
down_revision = "20261109_0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_incidents", sa.Column("batch_code", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_incidents", "batch_code")
