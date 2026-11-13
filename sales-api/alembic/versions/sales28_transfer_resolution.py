"""sales28_transfer_resolution — how a person settled a transfer's receipt difference (user 2026-11-13,
transfers 00104 / 00106: our 943 named the wrong item): "corrected" (the transfer becomes what arrived)
or "accepted" (the difference is real: lost or extra goods), with why, who and when.

Revision id "sales28_transfer_resolution" is 27 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op

revision = 'sales28_transfer_resolution'
down_revision = 'sales27_attachments'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sales_transfers', sa.Column('resolution', sa.String(20), nullable=True))
    op.add_column('sales_transfers', sa.Column('resolution_reason', sa.Text(), nullable=True))
    op.add_column('sales_transfers', sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('sales_transfers', sa.Column('resolved_by_name', sa.String(255), nullable=True))
    op.create_check_constraint('ck_sales_transfers_resolution', 'sales_transfers', "resolution IN ('corrected', 'accepted')")


def downgrade() -> None:
    op.drop_constraint('ck_sales_transfers_resolution', 'sales_transfers', type_='check')
    for c in ('resolved_by_name', 'resolved_at', 'resolution_reason', 'resolution'):
        op.drop_column('sales_transfers', c)
