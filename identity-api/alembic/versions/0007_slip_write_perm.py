"""Split pickup-slip recording out of epms.agreement.write into its own key.

Recording a pickup slip and editing an agreement's own terms were sharing one
gate (epms.agreement.write, label "Create / Edit Agreements") — anyone who
could tick that box in the Access Control matrix could also rewrite the
agreement's vendor/terms/schedule, which the person entering paper slips at
the counter has no business doing. This migration registers a dedicated key,
epms.agreement.slip.write ("Record Pickup Slips"), that epms-api's
agreement_slips.py / agreement_slip_attachments.py now gate the create/
update/void/attachment-write routes on instead.

Unlike 0006_agreement_perms, this migration does NOT seed every role that
currently has epms.agreement.write. That earlier migration's seeded-grants
rationale (documented there) was specifically to avoid 403'ing an existing
user who already had a live button — the Create-GR cutover's mistake. Pickup
slips have not shipped to production yet, so there is no incumbent user
whose button would vanish; there is nothing to preserve. The three roles
below are the caller's explicit starting choice (AP handles the paper trail,
Dept Admin covers house-account requesters' back office); every other role
that should be able to record a slip — including procurement_officer /
procurement_manager, deliberately left out here — is the caller's own call
to make in the Access Control matrix, not this migration's.

Fix-round 1 (Critical): dept_admin was NOT in 0006's epms.agreement.read
grant set (that migration's read list is procurement/AP/finance/approval-
chain roles + requester — dept_admin was never one of them). Granting it
ONLY epms.agreement.slip.write, as the first cut of this migration did, is a
key that opens a door dept_admin can't reach: GET /agreements/{id} (the page
itself) and GET .../slips both 403 first, and the sidebar doesn't even
render the Agreements nav entry (gated on epms.agreement.read), so an admin
who dutifully ticks "Record Pickup Slips" for Department Admin per the
caller's own instruction hands out a permission with zero observable effect
— dept_admin can't get far enough into the UI to use it, and nothing tells
the admin they also needed to tick "View Agreements". So this migration
grants epms.agreement.read to dept_admin too: not a scope expansion beyond
intent (the caller asked for dept_admin to be able to record slips, and
recording requires reaching the page first), just closing the gap between
"has the write key" and "can actually use it".

Idempotent (ON CONFLICT DO NOTHING); safe on a DB the seed scripts already
touched (epms.agreement.read already exists from 0006 — this only adds one
new role_permissions row for it) and self-sufficient on a fresh one.

Revision id is 20 chars — alembic_version_identity.version_num is varchar(32).
"""
from alembic import op

revision = "0007_slip_write_perm"
down_revision = "0006_agreement_perms"
branch_labels = None
depends_on = None

_KEYS = {
    "epms.agreement.slip.write": ("epms", "Record Pickup Slips", 106),
}

_GRANTS = {
    "epms.agreement.slip.write": ("system_admin", "ap_clerk", "dept_admin"),
    # dept_admin needs to be able to REACH the agreement detail page (and
    # GET .../slips) before "Record Pickup Slips" means anything — see the
    # fix-round-1 docstring note above. epms.agreement.read's permission_defs
    # row already exists (0006_agreement_perms); this is only a new grant row.
    "epms.agreement.read": ("dept_admin",),
}


def upgrade() -> None:
    for key, (module, label, sort) in _KEYS.items():
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','{module}','{label}',{sort}) ON CONFLICT (key) DO NOTHING")
    for key, roles in _GRANTS.items():
        for role in roles:
            op.execute(
                "INSERT INTO role_permissions(role_code,permission_key) "
                f"VALUES ('{role}','{key}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Only the grants + defs this migration added. Do NOT touch role_defs —
    # every role referenced here pre-exists. epms.agreement.read's
    # permission_defs row is NOT this migration's to delete (0006 owns it) —
    # only the dept_admin grant row this migration added to it.
    for key in _KEYS:
        op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{key}'")
        op.execute(f"DELETE FROM permission_defs WHERE key = '{key}'")
    op.execute(
        "DELETE FROM role_permissions "
        "WHERE role_code = 'dept_admin' AND permission_key = 'epms.agreement.read'")
