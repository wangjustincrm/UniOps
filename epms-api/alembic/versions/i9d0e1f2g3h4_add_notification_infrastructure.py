"""add notification infrastructure

Revision ID: i9d0e1f2g3h4
Revises: h8c9d0e1f2g3
Create Date: 2026-04-12 00:00:00.000000

"""
from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision: str = 'i9d0e1f2g3h4'
down_revision: Union[str, None] = 'h8c9d0e1f2g3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users: notification_channel ──────────────────────────────────────────
    op.add_column('users', sa.Column(
        'notification_channel', sa.String(20), nullable=True
    ))
    op.execute("UPDATE users SET notification_channel = 'email_only' WHERE notification_channel IS NULL")
    op.alter_column('users', 'notification_channel', nullable=False, server_default='email_only')

    # ── company_config: email_templates + notification_settings ──────────────
    op.add_column('company_config', sa.Column('email_templates', JSONB, nullable=True))
    op.add_column('company_config', sa.Column('notification_settings', JSONB, nullable=True))
    op.execute("UPDATE company_config SET email_templates = '{}' WHERE email_templates IS NULL")
    op.execute("UPDATE company_config SET notification_settings = '{\"default_channel\": \"email_only\"}' WHERE notification_settings IS NULL")
    op.alter_column('company_config', 'email_templates', nullable=False)
    op.alter_column('company_config', 'notification_settings', nullable=False)

    # ── notification_logs table ───────────────────────────────────────────────
    op.create_table(
        'notification_logs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('task_id', UUID(as_uuid=True), sa.ForeignKey('tasks.id', ondelete='SET NULL'), nullable=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('recipient_email', sa.String(255), nullable=True),
        sa.Column('channel', sa.String(20), nullable=False),
        sa.Column('template_key', sa.String(60), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='ok'),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('attempt', sa.Integer, nullable=False, server_default='1'),
        sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_notification_logs_task_id', 'notification_logs', ['task_id'])
    op.create_index('ix_notification_logs_user_id', 'notification_logs', ['user_id'])
    op.create_index('ix_notification_logs_template_key', 'notification_logs', ['template_key'])
    op.create_index('ix_notification_logs_status', 'notification_logs', ['status'])
    op.create_index('ix_notification_logs_sent_at', 'notification_logs', ['sent_at'])


def downgrade() -> None:
    op.drop_table('notification_logs')
    op.drop_column('company_config', 'notification_settings')
    op.drop_column('company_config', 'email_templates')
    op.drop_column('users', 'notification_channel')
