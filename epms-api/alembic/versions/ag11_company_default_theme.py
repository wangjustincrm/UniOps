"""ag11: company default UI theme

Revision ID: ag11_company_default_theme
Revises: ag10_agreement_deposit
Create Date: 2026-11-18

company_config.default_theme — the theme every module shows to a user who has
not picked one in their user menu (Shell themes: "uniops", "crm" …). Served
by the public branding endpoint so the web apps apply it before sign-in.
Additive with a server default: services that map company_config with their
own models ignore the column, so migration vs. container start order does not
matter.
"""
import sqlalchemy as sa
from alembic import op

revision = "ag11_company_default_theme"
down_revision = "ag10_agreement_deposit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_config",
        sa.Column("default_theme", sa.String(32), nullable=False, server_default="uniops"),
    )


def downgrade() -> None:
    op.drop_column("company_config", "default_theme")
