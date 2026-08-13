"""Split agreement-receipt recording out of epms.agreement.write into its own key.

Recording an agreement receipt (counter slip / delivery / service — see
epms-api's AgreementReceipt.receipt_type) and editing an agreement's own terms
were sharing one gate (epms.agreement.write, label "Create / Edit Agreements")
— anyone who could tick that box in the Access Control matrix could also
rewrite the agreement's vendor/terms/schedule, which the person entering
delivery evidence at the counter or from a courier slip has no business
doing. This migration registers a dedicated key,
epms.agreement.receipt.write ("Record Agreement Receipts (needs View
Agreements)" — see the _KEYS note below for why the label says that), that
epms-api's agreement_receipts.py / agreement_receipt_attachments.py gate the
create/update/void/attachment-write routes on instead.

Unlike 0006_agreement_perms, this migration does NOT seed every role that
currently has epms.agreement.write. That earlier migration's seeded-grants
rationale (documented there) was specifically to avoid 403'ing an existing
user who already had a live button — the Create-GR cutover's mistake. Agreement
receipts have not shipped to production yet, so there is no incumbent user
whose button would vanish; there is nothing to preserve. The three roles
below are the caller's explicit starting choice (AP handles the paper trail,
Dept Admin covers house-account requesters' back office); every other role
that should be able to record a receipt — including procurement_officer /
procurement_manager, deliberately left out here — is the caller's own call
to make in the Access Control matrix, not this migration's.

Fix-round 1 (Critical): dept_admin was NOT in 0006's epms.agreement.read
grant set (that migration's read list is procurement/AP/finance/approval-
chain roles + requester — dept_admin was never one of them). Granting it
ONLY epms.agreement.receipt.write, as the first cut of this migration did, is
a key that opens a door dept_admin can't reach: GET /agreements/{id} (the
page itself) and GET .../receipts both 403 first, and the sidebar doesn't
even render the Agreements nav entry (gated on epms.agreement.read), so an
admin who dutifully ticks "Record Agreement Receipts" for Department Admin
per the caller's own instruction hands out a permission with zero observable
effect — dept_admin can't get far enough into the UI to use it, and nothing
tells the admin they also needed to tick "View Agreements". So this
migration grants epms.agreement.read to dept_admin too: not a scope
expansion beyond intent (the caller asked for dept_admin to be able to
record receipts, and recording requires reaching the page first), just
closing the gap between "has the write key" and "can actually use it".

Task 11 fix-round 1 (Critical, second occurrence of the SAME pairing bug
in this branch — invoices.py:549-576 documents the first, Task 10 round 2
Finding B, and names cfo/erp_pa_officer explicitly): the PA chain
attachments panel (useChainAttachments.ts) added an agreement-receipt
evidence branch that calls GET /agreements/{id}/receipts and
.../receipts/{id}/attachments — both gated on epms.agreement.read — for ANY
viewer of a PA's attachment summary, not just agreement-page visitors. cfo
and erp_pa_officer both hold view_pa (seed_authz.py PERMISSIONS) and can
open that panel on any house_account PA, but neither was in
epms.agreement.read's grant set, so the receipt-detail and
receipt-attachment queries 403 and the panel falls back to "showing what's
available" — invoice PDF only, zero receipt photos, for the one role (cfo,
who additionally holds pa_override_receipt) this evidence package exists to
serve. Same fix shape as the dept_admin case above: grant the read key to
the two roles that hold the downstream capability (view_pa) but not the read
key it depends on — not a scope expansion, just closing the same "has the
button, can't reach the page" gap one layer over. No 0008 migration: this
branch has not been deployed to any environment yet (dev's
alembic_version_identity is still 0006), so there is no incumbent grant to
preserve and no reason to stack a second migration on an unshipped one —
fold the correction into this file directly.

Task 4 (this revision): the permission key itself was originally seeded and
reviewed under a "pickup slip" working name (module epms, key segments
agreement / slip / write, label "Record Pickup Slips (needs View
Agreements)") — the branch's first cut modeled every receipt as a counter
pickup slip. The caller generalized the concept to three receipt types
(counter_slip / delivery / service) before this migration ever reached any
environment (dev's alembic_version_identity is still 0006, same as above),
so there is no incumbent grant under the old key name to preserve either —
this revision is a straight rename, not an additive one. Renamed in-place
rather than filed as 0008 for the identical reason the Task 11 fix-round 1
correction above was folded into 0007 rather than stacked as its own
migration.

Idempotent (ON CONFLICT DO NOTHING); safe on a DB the seed scripts already
touched (epms.agreement.read already exists from 0006 — this only adds new
role_permissions rows for it) and self-sufficient on a fresh one.

Revision id is 23 chars — alembic_version_identity.version_num is varchar(32).
"""
from alembic import op

revision = "0007_receipt_write_perm"
down_revision = "0006_agreement_perms"
branch_labels = None
depends_on = None

# ⚠️ The label carries a dependency hint on purpose (whole-branch review I5).
# Access Control renders one independent checkbox per permission key with no
# notion of "X needs Y". epms.agreement.receipt.write is useless without
# epms.agreement.read: no read grant means no Agreements nav entry, no
# GET /agreements/{id} and no GET .../receipts — so ticking only "Record
# Agreement Receipts" for, say, Warehouse Staff produces a role that can see
# nothing and reach nothing, with no error message anywhere to explain it.
# That is precisely the dept_admin accident documented above, and the
# default matrix below being closed does not prevent it: the hole opens when
# an admin grants this key by hand later. The label is the only surface the
# checkbox actually shows, so the hint goes in the label.
#
# Kept byte-for-byte identical in identity-api/scripts/seed_phase2_keys.py.
_KEYS = {
    "epms.agreement.receipt.write": ("epms", "Record Agreement Receipts (needs View Agreements)", 106),
}

_GRANTS = {
    "epms.agreement.receipt.write": ("system_admin", "ap_clerk", "dept_admin"),
    # dept_admin needs to be able to REACH the agreement detail page (and
    # GET .../receipts) before "Record Agreement Receipts" means anything —
    # see the fix-round-1 docstring note above. cfo/erp_pa_officer need the
    # same read key for a different reason (Task 11 fix-round 1, also
    # documented above): they don't record receipts, they view them through
    # a PA's attachment summary, which hits the same
    # epms.agreement.read-gated routes. epms.agreement.read's permission_defs
    # row already exists (0006_agreement_perms); these are only new grant
    # rows.
    "epms.agreement.read": ("dept_admin", "cfo", "erp_pa_officer"),
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
    # only the dept_admin/cfo/erp_pa_officer grant rows this migration added
    # to it.
    for key in _KEYS:
        op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{key}'")
        op.execute(f"DELETE FROM permission_defs WHERE key = '{key}'")
    op.execute(
        "DELETE FROM role_permissions "
        "WHERE role_code IN ('dept_admin', 'cfo', 'erp_pa_officer') "
        "AND permission_key = 'epms.agreement.read'")
