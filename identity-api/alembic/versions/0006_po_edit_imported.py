"""Add epms.po.edit_imported and grant it to erp_pa_officer.

NC-imported POs land as status='issued' carrying only what NC holds — no
supplier item IDs, no sample requirements, no Incoterms, no delivery detail.
Filling those in is gated by this key rather than by epms.po.write, because
epms.po.write also opens PATCH /po/{id}, which can replace the vendor, the
currency and the whole line-item set.

Seeding the grant here rather than leaving it for an admin to tick in
Portal -> Access Control is deliberate: the Create-GR cutover shipped a
matrix-driven gate with no seeded grant and 403'd everyone who previously had
the button.

system_admin is NOT granted explicitly — require_permission short-circuits it.

Idempotent (ON CONFLICT DO NOTHING), so it is safe on a database the seed
scripts already touched and self-sufficient on a fresh one. Unlike migration
0005 it does not insert a role_defs row: erp_pa_officer was created by
0004_erp_pa_officer_role, which this migration transitively follows.

Revision id length: alembic_version_identity.version_num is varchar(32);
"0006_po_edit_imported" is 20 characters.
"""
from alembic import op

revision = "0006_po_edit_imported"
down_revision = "0005_procurement_officer_pa"
branch_labels = None
depends_on = None

_ROLE = "erp_pa_officer"
_KEY = "epms.po.edit_imported"


def upgrade() -> None:
    op.execute(
        "INSERT INTO permission_defs(key,module,label,sort) "
        f"VALUES ('{_KEY}','epms','Edit Imported (NC) POs',104) "
        "ON CONFLICT (key) DO NOTHING")
    op.execute(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"VALUES ('{_ROLE}','{_KEY}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Only the grant and the key this migration introduced. role_defs is shared
    # with the seed scripts and other roles' grants — leave it alone.
    op.execute(
        f"DELETE FROM role_permissions WHERE permission_key = '{_KEY}'")
    op.execute(f"DELETE FROM permission_defs WHERE key = '{_KEY}'")
