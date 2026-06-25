"""Add VMS-local SMTP settings to vms_config.

Up to now VMS borrowed `company_config.smtp_*` (epms-api owns that table)
for outbound email. Operators want VMS-specific SMTP — a different
mailbox, e.g. `vms@canadaroyalmilk.com` — without touching the shared
EPMS config. New field is a JSONB blob so we can add settings (auth
method, BCC list, etc.) without further migrations.

Expected shape (all optional):
    {
      "host":       "smtp.gmail.com",
      "port":       587,
      "user":       "vms@example.com",
      "password":   "...",
      "use_tls":    true,
      "from_email": "vms@example.com"
    }

When this blob is empty `_load_smtp_config` falls back to the EPMS row so
existing deployments keep working.

Revision ID: 20260602_0010
Revises: 20260602_0009
Create Date: 2026-06-02
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260602_0010"
down_revision = "20260602_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vms_config",
        sa.Column(
            "smtp_settings",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("vms_config", "smtp_settings")
