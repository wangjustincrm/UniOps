"""Give bank reconciliation and bank settings their own Access Control keys.

Both were gated on finance.coa.manage — "Manage Chart of Accounts" — because
that was the finance write key that existed when the pages were built. The
matrix therefore had no row for either, and the only way to let a Payment
Officer reconcile a bank account was to let them edit the chart of accounts.
That is what happened in production on 2026-09-25: payment_officer was granted
finance.coa.manage, finance.jv.post and finance.period.close together, in one
edit, while looking for a bank permission that was not there.

Two keys, not one: reconciling (importing statements, matching, signing off)
and maintaining the bank-account master (which NC account an account IS) are
different jobs, and the second decides what every reconciliation reads.

Grants: every role that holds finance.coa.manage when this runs keeps what it
could do before — copied from the matrix as it stands, not from a list typed
here — plus payment_officer, the role this was built for. system_admin is
granted explicitly for the same reason 0013 gives: effective_permissions()
does not short-circuit it.

Idempotent (ON CONFLICT DO NOTHING). payment_officer and system_admin are
granted only if role_defs has them, so a fresh database whose roles are seeded
later does not abort on the foreign key.

Revision id length: "0014_bank_perms" is 15 characters (limit 32).
"""
from alembic import op

revision = "0014_bank_perms"
down_revision = "0013_view_system_settings"
branch_labels = None
depends_on = None

_KEYS = (
    ("finance.bank.reconcile", "Bank Reconciliation", 115),
    ("finance.bank.settings", "Manage Bank Accounts", 116),
)
_COPY_FROM = "finance.coa.manage"
_ALSO = ("system_admin", "payment_officer")


def upgrade() -> None:
    for key, label, sort in _KEYS:
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','finance','{label}',{sort}) "
            "ON CONFLICT (key) DO NOTHING")
        op.execute(
            "INSERT INTO role_permissions(role_code,permission_key) "
            f"SELECT role_code, '{key}' FROM role_permissions "
            f"WHERE permission_key = '{_COPY_FROM}' ON CONFLICT DO NOTHING")
        for role in _ALSO:
            op.execute(
                "INSERT INTO role_permissions(role_code,permission_key) "
                f"SELECT code, '{key}' FROM role_defs WHERE code = '{role}' "
                "ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Grants only, as in 0012/0013: the keys may carry grants an admin added in
    # Portal -> Access Control since, and the gates in finance-api refuse
    # everyone but system_admin once they are gone.
    for key, _label, _sort in _KEYS:
        op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{key}'")
