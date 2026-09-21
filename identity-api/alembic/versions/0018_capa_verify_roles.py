"""Widen who may verify a corrective action, as HSE asked.

The second acceptance round answered a question we had put to them:

    "every role should be able to verify an action if it is assigned to them.
     HSE, JHSC and supervisor roles should be able to verify all CAPA action
     items."

That is their process and this implements it. It is worth recording what it
changes, because it is not a small thing: the module was built so that whoever
did the work could not sign it off, which is the second-person check COR and
ISO 45001 both look for. Under this, an owner verifying their own action is
allowed.

Nothing is hidden by it. The verification stores who performed it beside who
owned the action, and the audit pack shows both, so a self-verification is
visible to an auditor rather than indistinguishable from an independent one.

This migration does the "all CAPA items" half — the roles. The other half,
an owner verifying their own, is a rule in the code: it does not correspond to
a permission, since it depends on the row.

Revision ID: 0018_capa_verify
Revises: 0017_worker_holders
Create Date: 2026-09-22
"""
from alembic import op

revision = "0018_capa_verify"
down_revision = "0017_worker_holders"
branch_labels = None
depends_on = None

_KEY = "ehs.action.verify"
_ADDED = ("area_supervisor", "jhsc_member")


def upgrade() -> None:
    for role in _ADDED:
        op.execute(
            "INSERT INTO role_permissions(role_code,permission_key) "
            f"VALUES ('{role}','{_KEY}') ON CONFLICT DO NOTHING"
        )


def downgrade() -> None:
    roles = ", ".join(f"'{r}'" for r in _ADDED)
    op.execute(
        f"DELETE FROM role_permissions WHERE permission_key = '{_KEY}' "
        f"AND role_code IN ({roles})"
    )
