"""Add view_system_settings and grant it to system_admin.

The assistant can answer about purchasing, finance, MRP and master data, but
asked "how many users are there, and how many are active" it replied that the
system holds no user information. It holds 112, of which 81 are active. The
data was never the problem — there was no permission to gate it behind, so
there was no entity, so there was nothing to find.

A new key rather than an existing one. `admin_panel` is the closest fit and is
wrong: it is held by finance_manager and vendor_manager as well, while the
users endpoint it would be standing in for is require_roles("system_admin").
Reusing it would have quietly widened who can enumerate staff, roles and
company configuration — a change nobody asked for, made as a side effect of a
convenience.

★ system_admin IS granted explicitly here, unlike every other migration in this
directory, and that is deliberate. The other keys are read through
require_permission(), which short-circuits system_admin before consulting the
matrix. The assistant reads its gates from effective_permissions(), which by
design does NOT short-circuit — it returns the matrix as it stands. Leaving
system_admin implicit would produce exactly the failure this migration exists
to fix, for the one role most likely to ask.

Idempotent (ON CONFLICT DO NOTHING): safe on a database the seed scripts have
already touched, self-sufficient on a fresh one.

Revision id length: alembic_version_identity.version_num is varchar(32);
"0013_view_system_settings" is 25 characters.
"""
from alembic import op

revision = "0013_view_system_settings"
down_revision = "0012_po_signoff_perm"
branch_labels = None
depends_on = None

_KEY = "view_system_settings"


def upgrade() -> None:
    op.execute(
        "INSERT INTO permission_defs(key,module,label,sort) "
        f"VALUES ('{_KEY}','portal','View System Settings',20) "
        "ON CONFLICT (key) DO NOTHING")
    op.execute(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"VALUES ('system_admin','{_KEY}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # The grant only. permission_defs is shared with the seed scripts and any
    # grants an admin has since added in Portal -> Access Control; deleting the
    # key would take those with it through the foreign key.
    op.execute(
        f"DELETE FROM role_permissions WHERE permission_key = '{_KEY}' "
        "AND role_code = 'system_admin'")
