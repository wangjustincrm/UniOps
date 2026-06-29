"""add password_changed_at to users

Supports password-expiry enforcement (Admin → Security → Password Expiry).
identity-api login compares now() - password_changed_at against
company_config.password_expiry_days and forces a change when exceeded.

Existing rows are backfilled to now() so nobody is instantly expired by the
migration itself; the standard initial-password reset stamps it going forward.

Revision ID: z2_add_password_changed_at
Revises: z1_add_prepayment_applied
Create Date: 2026-06-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'z2_add_password_changed_at'
down_revision: Union[str, None] = 'z1_add_prepayment_applied'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column(
        'password_changed_at', sa.DateTime(timezone=True), nullable=True
    ))
    # Backfill existing accounts so they are not treated as expired before they
    # have ever rotated their password under the new policy.
    op.execute("UPDATE users SET password_changed_at = now() WHERE password_changed_at IS NULL")


def downgrade() -> None:
    op.drop_column('users', 'password_changed_at')
