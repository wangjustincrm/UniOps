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

Idempotent (ON CONFLICT DO NOTHING); safe on a DB the seed scripts already
touched and self-sufficient on a fresh one.

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
    # every role referenced here pre-exists.
    for key in _KEYS:
        op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{key}'")
        op.execute(f"DELETE FROM permission_defs WHERE key = '{key}'")
