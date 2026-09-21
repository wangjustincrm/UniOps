"""Permission to maintain the plant area tree.

The tree has been read-only since the day it was seeded: `locations` lives in
mdm-api, mdm-api never had an endpoint for it, and the only way a node has ever
been added is by writing another migration. HSE has been unable to add the
Line 1 / Line 2 nodes they told us about in PRD question 6.7, which is why
incidents on those lines are still filed against the area above them.

One key, not the usual read/write pair: reading the tree is already open to
everybody who can file anything against it, and adding a second key would mean
re-granting a read permission that eight roles already have by other means.

Granted narrowly. An area added here becomes a choice on every incident form
in the plant and a QR sticker on a door; it is not a thing to let drift.
`system_admin` is included because someone has to be able to fix it when the
HSE Manager is away, which is the same reason it holds the other mdm keys.

Revision id length: alembic_version_identity.version_num is varchar(32);
"0019_location_perms" is 19 characters.
"""
from alembic import op

revision = "0019_location_perms"
down_revision = "0018_capa_verify"
branch_labels = None
depends_on = None

_KEY = "mdm.location.write"
_LABEL = "Maintain Plant Areas"
_SORT = 250

# Named rather than "every active role": a migration that grants to whatever is
# active silently skips any role an earlier migration retired, and this module
# has already been bitten by that once.
_ROLES = ["ehs_manager", "ehs_coordinator", "system_admin"]


def upgrade() -> None:
    op.execute(
        "INSERT INTO permission_defs(key,module,label,sort) "
        f"VALUES ('{_KEY}','mdm','{_LABEL}',{_SORT}) "
        "ON CONFLICT (key) DO NOTHING"
    )
    for role in _ROLES:
        # The insert is guarded on the role actually existing: role_permissions
        # carries a foreign key to role_defs, and a grant to a role code that
        # is not there aborts the migration mid-run — in production, halfway
        # through the deployment.
        op.execute(
            "INSERT INTO role_permissions(role_code,permission_key) "
            f"SELECT '{role}','{_KEY}' WHERE EXISTS "
            f"(SELECT 1 FROM role_defs WHERE code='{role}') "
            "ON CONFLICT DO NOTHING"
        )


def downgrade() -> None:
    op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{_KEY}'")
    op.execute(f"DELETE FROM role_permission_locks WHERE permission_key = '{_KEY}'")
    op.execute(f"DELETE FROM permission_defs WHERE key = '{_KEY}'")
