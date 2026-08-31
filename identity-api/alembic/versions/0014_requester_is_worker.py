"""There is no such thing as a "worker" role here — everyone is one.

Safety was modelled with a `worker` role because SafetyAmp and SiteDocs both
have one. In those systems it earns its keep: they hold their own user list, so
somebody has to be marked as plant staff. UniOps already gives every employee an
account, which makes `worker` a role that means "an employee" — true of all of
them, and therefore carrying no information.

The evidence is in what it grants. `worker` differs from EPMS's `requester` by
exactly one key, `ehs.action.read`, and that key only lets you open the
corrective actions assigned to *you* — browsing everybody's is gated on
`ehs.incident.read` (see `_may_browse` in actions.py). So it is not a privilege
that needs handing out selectively; withholding it only breaks the person who
was assigned the work.

And withholding it is exactly what was happening. 29 of the 118 accounts here
are neither requesters nor HSE staff — 13 department managers, 7 AP clerks, 4
procurement officers, 4 admins, a warehouse lead — and none of them could open
My Actions. A department manager receiving a corrective action is not an edge
case: it is step three of the CAPA escalation chain HSE specified (overdue 5
days → owner + supervisor). Granting `worker` to the 84 requesters, which is
where this migration started, would have left all 29 of them behind.

So the key goes to every active role, on the same reasoning 0013 used for
`ehs.incident.report`: uniops_authz resolves each key to an explicit true/false
with no fallback, so a key nobody was granted denies everyone.

With that done `worker` grants nothing its holders lack, and it is deactivated.
Deactivation rather than deletion because `role_permissions` rows, and any
`tasks.assigned_role='worker'` already written, stay readable. Note the two
sources resolve differently in uniops_authz: additional roles join on
`rd.is_active` and stop counting, while a primary role is taken at face value —
so anyone whose *primary* role is `worker` keeps working, and the Access Control
UI shows it as "(not selectable)".

Revision ID: 0014_requester_is_worker
Revises: 0013_ehs_perms
Create Date: 2026-08-31
"""
from alembic import op

revision = "0014_requester_is_worker"
down_revision = "0013_ehs_perms"
branch_labels = None
depends_on = None

# Held ehs.action.read before this migration widened it; downgrade restores
# exactly this set rather than guessing.
_ORIGINAL_HOLDERS = (
    "area_supervisor", "auditor", "ehs_coordinator",
    "ehs_manager", "jhsc_member", "worker",
)


def upgrade() -> None:
    # Runs while `worker` is still active, so it is covered like every other role.
    op.execute("""
        insert into role_permissions (role_code, permission_key)
        select rd.code, 'ehs.action.read' from role_defs rd where rd.is_active
        on conflict do nothing
    """)
    op.execute("update role_defs set is_active = false where code = 'worker'")


def downgrade() -> None:
    op.execute("update role_defs set is_active = true where code = 'worker'")
    holders = ", ".join(f"'{c}'" for c in _ORIGINAL_HOLDERS)
    op.execute(f"""
        delete from role_permissions
         where permission_key = 'ehs.action.read'
           and role_code not in ({holders})
    """)
