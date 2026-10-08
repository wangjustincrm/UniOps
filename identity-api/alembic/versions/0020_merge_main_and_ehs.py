"""Join the Safety branch's migrations to main's, and re-apply the everyone grants.

Safety: 0013_ehs_perms .. 0019_location_perms (roles, keys, retiring worker).
main:   0013_view_system_settings .. 0015_finance_director_role.

Both lines start from 0012 and touch no common rows, with one exception that
depends on the order they run in. Four Safety keys go to every active role —
ehs.incident.report (0013), ehs.action.read (0014), ehs.document.read (0015),
ehs.permit.request (0016) — written as a SELECT over role_defs, which covers
the roles that exist *when it runs*. Production is on main's line already, so
there the Safety line runs after finance_director exists and grants it all
four. Dev was on the Safety line, so main's 0015 created finance_director
afterwards and it got none. Both orders were run on copies of the dev database
and the grants compared; that was the only difference.

So the same four grants are made again here, for every active role, whichever
order brought us here. Idempotent: a role that has a key keeps it.

Downgrade removes nothing. These grants are what the Safety line already
intends for every role; a merge point is not the place to take them away, and
it could not tell which rows it added.

Revision ID: 0020_merge_main_and_ehs
Revises: 0019_location_perms, 0015_finance_director_role
"""
from alembic import op

revision = "0020_merge_main_and_ehs"
down_revision = ("0019_location_perms", "0015_finance_director_role")
branch_labels = None
depends_on = None

_EVERYONE = (
    "ehs.incident.report",
    "ehs.action.read",
    "ehs.document.read",
    "ehs.permit.request",
)


def upgrade() -> None:
    for key in _EVERYONE:
        op.execute(
            "INSERT INTO role_permissions(role_code, permission_key) "
            f"SELECT code, '{key}' FROM role_defs WHERE is_active "
            "ON CONFLICT DO NOTHING"
        )


def downgrade() -> None:
    pass
