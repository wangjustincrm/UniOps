"""sales08_outbound_940 — what sending 940s needs (P4).

- `sales_x12_control_seq`: the one shared 9-digit control sequence for every
  outbound X12 file (FR-OB-22): ISA13 = GS06 = ST02, allocated in the sending
  transaction, never reused — a resend takes a new number (AC-OB-29).
- `sales_documents.sent_at`: when the file was put, for the 997 timeout
  (FR-OB-23); `ack_detail`: the 997's codes (AK5 reasons) or the 945 that
  accepted it.
- `sales_orders.shipping_method`: the Shopify shipping line (W66 GND / EXP, A.3).

Revision id "sales08_outbound_940" is 19 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa

revision = 'sales08_outbound_940'
down_revision = 'sales07_user_views'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence('sales_x12_control_seq', start=1, minvalue=1, maxvalue=999999999)))
    op.add_column('sales_documents', sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('sales_documents', sa.Column('ack_detail', sa.Text(), nullable=True))
    op.add_column('sales_orders', sa.Column('shipping_method', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('sales_orders', 'shipping_method')
    op.drop_column('sales_documents', 'ack_detail')
    op.drop_column('sales_documents', 'sent_at')
    op.execute(sa.schema.DropSequence(sa.Sequence('sales_x12_control_seq')))
