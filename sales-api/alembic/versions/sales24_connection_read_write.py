"""sales24_connection_read_write — what Sales may do on a channel: two ticks instead of a mode.

User, 2026-11-12: no Test / Live mode and no test folders; each connection says whether
Sales may read it and whether it may write to it. Every connection starts as it behaved
(a Test connection read and never wrote to its real folders): can_read true, can_write
false — a person turns writing on. The test folders go; a test outbound folder still set is
refused (it would be lost silently): clear it first.

Revision id "sales24_connection_read_write" is 29 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op

revision = 'sales24_connection_read_write'
down_revision = 'sales23_order_edi_mailbox'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    left = bind.execute(sa.text(
        "SELECT name FROM sales_connections WHERE coalesce(trim(test_outbound_folder), '') <> ''")).scalars().all()
    if left:
        raise RuntimeError("these connections still name a test outbound folder; move it to the outbound folder or "
                           f"clear it, then upgrade: {', '.join(left)}")
    op.add_column('sales_connections', sa.Column('can_read', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('sales_connections', sa.Column('can_write', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.drop_constraint('ck_sales_connections_mode', 'sales_connections', type_='check')
    op.drop_column('sales_connections', 'mode')
    op.drop_column('sales_connections', 'test_inbound_folder')
    op.drop_column('sales_connections', 'test_outbound_folder')


def downgrade() -> None:
    op.add_column('sales_connections', sa.Column('test_outbound_folder', sa.Text(), nullable=True))
    op.add_column('sales_connections', sa.Column('test_inbound_folder', sa.Text(), nullable=True))
    op.add_column('sales_connections', sa.Column('mode', sa.String(length=10), nullable=False, server_default='TEST'))
    op.execute("UPDATE sales_connections SET mode = CASE WHEN can_write THEN 'LIVE' ELSE 'TEST' END")
    op.create_check_constraint('ck_sales_connections_mode', 'sales_connections', "mode IN ('LIVE', 'TEST')")
    op.drop_column('sales_connections', 'can_write')
    op.drop_column('sales_connections', 'can_read')
