"""Add epms.po.signoff and grant it to erp_pa_officer.

Raising the sign-off of an NC-imported PO is the ERP PA Officer's job — the
same people who fill in the buyer detail an NC import does not carry (see
0011_po_edit_imported). It gets its own key rather than reusing
epms.po.edit_imported: filling in Incoterms is not the same authority as
putting a purchase in front of the Purchasing Manager and the OPM for
signature, and an admin must be able to grant one without the other.

Nothing gates SIGNING on a permission key. approval-api admits the holder of
the step's configured role and nobody else, which is the only correct gate; a
second, key-based check could only ever disagree with it.

Seeding the grant here rather than leaving it for an admin to tick in
Portal → Access Control follows 0011: a matrix-driven gate that ships with no
grant 403s everyone who is supposed to use it on day one.

system_admin is NOT granted explicitly — require_permission short-circuits it.

Revision id length: alembic_version_identity.version_num is varchar(32);
"0012_po_signoff_perm" is 20 characters.
"""
from alembic import op

revision = "0012_po_signoff_perm"
down_revision = "0011_po_edit_imported"
branch_labels = None
depends_on = None

_ROLE = "erp_pa_officer"
_KEY = "epms.po.signoff"


def upgrade() -> None:
    op.execute(
        "INSERT INTO permission_defs(key,module,label,sort) "
        f"VALUES ('{_KEY}','epms','Raise PO Sign-off',109) "
        "ON CONFLICT (key) DO NOTHING")
    op.execute(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"VALUES ('{_ROLE}','{_KEY}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Only the grant this migration added — permission_defs is shared with
    # seed_phase2_keys.py, and dropping the key would break system_admin's row
    # by foreign key. Same reasoning as 0011.
    op.execute(
        f"DELETE FROM role_permissions WHERE role_code = '{_ROLE}' "
        f"AND permission_key = '{_KEY}'")
