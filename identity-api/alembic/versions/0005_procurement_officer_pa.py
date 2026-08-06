"""Grant epms.pa.write to procurement_officer (create PA on behalf of anyone).

A Procurement Officer must be able to raise a Payment Application against ANY
purchase order, not just POs linked to a requisition they raised. The write
gate is the Access Control Matrix key epms.pa.write, so the whole change is one
matrix cell.

Approval routing is deliberately untouched: approval-api resolves a PA's
approvers from the linked PR's requester/department, never from PA.created_by
(see approval-api/app/crud/engine.py::_routing_user_id), so paying on someone
else's behalf cannot redirect the approval chain.

Granting here (rather than leaving it for an admin to tick in Portal -> Access
Control) is deliberate: the Create-GR cutover shipped a matrix-driven gate with
no seeded grant and 403'd everyone who previously had the button.

Idempotent (ON CONFLICT DO NOTHING) so it is safe on a DB the seed scripts
already touched, and self-sufficient on a fresh DB (it seeds the permission_defs
row its grant references).

Note: the revision id is "0005_procurement_officer_pa" rather than the fuller
"..._pa_write" — alembic_version_identity.version_num is varchar(32), and the
longer id (33 chars) overflows it (confirmed by an actual failed upgrade run
against the local dev DB, which rolled back cleanly under transactional DDL).
"""
from alembic import op

revision = "0005_procurement_officer_pa"
down_revision = "0004_erp_pa_officer_role"
branch_labels = None
depends_on = None

_ROLE = "procurement_officer"
_KEY = "epms.pa.write"


def upgrade() -> None:
    op.execute(
        "INSERT INTO permission_defs(key,module,label,sort) "
        f"VALUES ('{_KEY}','epms','Create / Edit PAs',102) "
        "ON CONFLICT (key) DO NOTHING")
    # label/sort must mirror seed_authz.ROLE_LABELS — procurement_officer is a
    # built-in role seeded by enumerate(ROLE_LABELS) there, sitting at index 7
    # (requester=0, dept_admin=1, dept_manager=2, supervisor=3, director=4,
    # gm=5, opm=6, procurement_officer=7). Both inserts are ON CONFLICT DO
    # NOTHING, so whichever of this migration / seed_authz runs first on a
    # fresh DB wins — keeping the values identical means it doesn't matter
    # which one that is; a wrong sort here would otherwise pin the role out of
    # its built-in position in the Portal -> Access Control matrix forever.
    op.execute(
        "INSERT INTO role_defs(code,label,sort,is_active) "
        f"VALUES ('{_ROLE}','Procurement Officer',7,true) ON CONFLICT (code) DO NOTHING")
    op.execute(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"VALUES ('{_ROLE}','{_KEY}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Only the grant this migration added. permission_defs / role_defs are
    # shared with the seed scripts and other roles' grants — leave them alone.
    # In particular, do NOT delete the role_defs row: procurement_officer is a
    # pre-existing built-in role this migration did not introduce (the upgrade()
    # insert above only exists to satisfy role_permissions' FK on a fresh DB),
    # so deleting it here would cascade damage well beyond what this migration
    # granted.
    op.execute(
        f"DELETE FROM role_permissions WHERE role_code = '{_ROLE}' "
        f"AND permission_key = '{_KEY}'")
