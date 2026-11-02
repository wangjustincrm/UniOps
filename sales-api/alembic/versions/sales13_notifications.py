"""sales13_notifications — notification rules in use and their history (P8;
FR-OB-56, AC-OB-52 / 71).

- `sales_notification_rules` (from sales01) gets the last test notification:
  `tested_at` and `test_result` (the go-live gate, PRD section 11).
- `sales_notifications`: one row per notification made — a Task Inbox task or
  an email (its delivery is the outbox row's status) — with the event, the
  problem key, the recipients and the subject; `test` marks a rule's test.
  `outbox_id` is a plain reference (no foreign key): the outbox is truncated
  and pruned on its own.

Revision id "sales13_notifications" is 20 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'sales13_notifications'
down_revision = 'sales12_transfers'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sales_notification_rules', sa.Column('tested_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('sales_notification_rules', sa.Column('test_result', sa.Text(), nullable=True))
    op.create_table(
        'sales_notifications',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('problem_key', sa.String(length=40), nullable=True),
        sa.Column('channel', sa.String(length=10), nullable=False),
        sa.Column('recipients', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('subject', sa.Text(), nullable=False),
        sa.Column('task_id', sa.UUID(), nullable=True),
        sa.Column('outbox_id', sa.Integer(), nullable=True),
        sa.Column('test', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.CheckConstraint("channel IN ('TASK', 'EMAIL')", name='ck_sales_notifications_channel'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sales_notifications_at', 'sales_notifications', ['at'])
    op.create_index('ix_sales_notifications_event_type', 'sales_notifications', ['event_type'])


def downgrade() -> None:
    op.drop_index('ix_sales_notifications_event_type', table_name='sales_notifications')
    op.drop_index('ix_sales_notifications_at', table_name='sales_notifications')
    op.drop_table('sales_notifications')
    op.drop_column('sales_notification_rules', 'test_result')
    op.drop_column('sales_notification_rules', 'tested_at')
