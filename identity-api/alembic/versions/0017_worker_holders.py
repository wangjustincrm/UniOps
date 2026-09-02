"""Finish retiring `worker`: move whoever still holds it to `requester`.

0014 retired the role and argued the case: everyone in UniOps is an employee,
so a role meaning "an employee" carries no information. It deactivated rather
than deleted, on the grounds that anyone whose *primary* role was `worker`
would keep working — uniops_authz joins additional roles on `is_active` but
takes a primary role at face value.

That turned out to be half true, and the half that failed is the dangerous one.
Every grant in this module is written as "every active role", so the two
migrations that came afterwards passed `worker` by:

    0015  ehs.document.read     → every active role
    0016  ehs.permit.request    → every active role

`worker` was already inactive, so it got neither. It now holds two keys where
`requester` holds fourteen. Somebody left on it as a primary role is not
equivalent to a requester, which is what retiring it was supposed to mean —
they cannot open the safety data sheet for what they handle, which under WHMIS
is a right, and they cannot raise a work permit.

Topping the role back up would keep a role alive that was retired on purpose,
and would need doing again after every future "grant to every active role".
Retired should mean nobody holds it, so this moves the holders off instead.

Zero rows on any deployed database: `worker` was created by 0013, which has not
shipped. This matters on the Safety branch, where accounts were created against
it during development.

Revision ID: 0017_worker_holders
Revises: 0016_ehs_permit_perms
Create Date: 2026-09-02
"""
from alembic import op

revision = "0017_worker_holders"
down_revision = "0016_ehs_permit_perms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Primary role. `requester` is what every UniOps employee already is, and
    # is the role 0014 named as the equivalent.
    op.execute("update users set role = 'requester' where role = 'worker'")
    # Additional-role rows are already inert — uniops_authz joins them on
    # role_defs.is_active — but leaving them means the Access Control UI keeps
    # showing a retired chip against those people.
    op.execute("delete from user_roles where role_code = 'worker'")


def downgrade() -> None:
    # Not reversible: which of the requesters used to be workers is not
    # recorded anywhere, and guessing would hand the role to 84 accounts that
    # never had it.
    pass
