"""sales19_carriers — carriers and their tracking links (user 2026-11-03).

`sales_carriers`: the SCAC a 945 names (W27-02), the carrier's name and its
tracking link template with `{tracking}`. The table only: no seed data
(FR-OB-09) — carriers are added on Configuration → Carriers.

Revision id "sales19_carriers" is 15 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa

revision = 'sales19_carriers'
down_revision = 'sales18_line_invoice_price'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sales_carriers',
        sa.Column('scac', sa.String(length=10), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('tracking_url', sa.String(length=500), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sa.UUID(), nullable=True),
        sa.Column('updated_by_name', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_by_name', sa.String(length=255), nullable=False),
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scac', name='sales_carriers_scac_key'),
    )


def downgrade() -> None:
    op.drop_table('sales_carriers')
