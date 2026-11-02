"""sales14_conn_schedule — a schedule per connection (user decision 2026-11-02).

- `sales_connections`: `schedule_minutes` (≥ 1; 5 for SFTP, 10 for Shopify),
  `schedule_paused`, the optional active window — `active_from` / `active_to`
  (Toronto local time; an end before the start crosses midnight) and
  `active_days` ("Mon,Tue,…"; empty = every day) — and what the scheduler
  writes: `last_run_at`, `last_run_status`, `last_run_summary`,
  `last_error`, `next_run_at`.
- `sales_runs.scope`: what a run was for — "connection:<id>" or "system"
  (older runs, the one cycle: none).

Revision id "sales14_conn_schedule" is 20 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa

revision = 'sales14_conn_schedule'
down_revision = 'sales13_notifications'
branch_labels = None
depends_on = None


def upgrade() -> None:
    add = lambda *a, **k: op.add_column('sales_connections', sa.Column(*a, **k))  # noqa: E731
    add('schedule_minutes', sa.Integer(), server_default='5', nullable=False)
    add('schedule_paused', sa.Boolean(), server_default=sa.text('false'), nullable=False)
    add('active_from', sa.Time(), nullable=True)
    add('active_to', sa.Time(), nullable=True)
    add('active_days', sa.String(length=27), nullable=True)
    add('last_run_at', sa.DateTime(timezone=True), nullable=True)
    add('last_run_status', sa.String(length=10), nullable=True)
    add('last_run_summary', sa.String(length=255), nullable=True)
    add('last_error', sa.Text(), nullable=True)
    add('next_run_at', sa.DateTime(timezone=True), nullable=True)
    op.execute("UPDATE sales_connections SET schedule_minutes = 10 WHERE kind = 'SHOPIFY'")
    op.create_check_constraint('ck_sales_connections_schedule_minutes', 'sales_connections', 'schedule_minutes >= 1')
    op.create_check_constraint('ck_sales_connections_active_window', 'sales_connections',
                               '(active_from IS NULL) = (active_to IS NULL)')
    op.add_column('sales_runs', sa.Column('scope', sa.String(length=60), nullable=True))
    op.create_index('ix_sales_runs_scope', 'sales_runs', ['scope'])


def downgrade() -> None:
    op.drop_index('ix_sales_runs_scope', table_name='sales_runs')
    op.drop_column('sales_runs', 'scope')
    op.drop_constraint('ck_sales_connections_active_window', 'sales_connections', type_='check')
    op.drop_constraint('ck_sales_connections_schedule_minutes', 'sales_connections', type_='check')
    for c in ('next_run_at', 'last_error', 'last_run_summary', 'last_run_status', 'last_run_at', 'active_days',
              'active_to', 'active_from', 'schedule_paused', 'schedule_minutes'):
        op.drop_column('sales_connections', c)
