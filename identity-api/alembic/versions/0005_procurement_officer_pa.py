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
    op.execute(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"VALUES ('{_ROLE}','{_KEY}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Only the grant this migration added. permission_defs / role_defs are
    # shared with the seed scripts and other roles' grants — leave them alone.
    op.execute(
        f"DELETE FROM role_permissions WHERE role_code = '{_ROLE}' "
        f"AND permission_key = '{_KEY}'")
