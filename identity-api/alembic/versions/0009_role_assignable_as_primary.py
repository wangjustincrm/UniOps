"""role_defs.assignable_as_primary — additional-only roles can't be a login role.

`erp_pa_officer` (0004) and `payment_officer` (0008) are ADDITIONAL roles: they
are granted per user through `user_roles` and must never be written to
`users.role`. Until now that rule lived only in docstrings and in two frontend
hardcoded sets, so Portal's Access Control Primary Role dropdown — and any
direct API call — could still set them as a primary role. For payment_officer
that handed out full payment authority through finance-api's PRIMARY-role
short-circuit while bypassing the additional-role model entirely.

Making it a column (rather than a constant in identity) keeps the two admin
frontends from drifting: they filter their dropdowns on what /authz/defs says.

Every other role defaults to true, so existing assignments are untouched. This
migration does NOT rewrite any user who already holds one of these as a primary
role — see scripts/check_additional_only_primary.sql for the read-only audit.
"""
from alembic import op

revision = "0009_role_assignable_as_primary"
down_revision = "0008_payment_officer_role"
branch_labels = None
depends_on = None

_ADDITIONAL_ONLY = "'erp_pa_officer','payment_officer'"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE role_defs ADD COLUMN IF NOT EXISTS assignable_as_primary "
        "BOOLEAN NOT NULL DEFAULT true")
    op.execute(
        f"UPDATE role_defs SET assignable_as_primary = false "
        f"WHERE code IN ({_ADDITIONAL_ONLY})")


def downgrade() -> None:
    op.execute("ALTER TABLE role_defs DROP COLUMN IF EXISTS assignable_as_primary")
