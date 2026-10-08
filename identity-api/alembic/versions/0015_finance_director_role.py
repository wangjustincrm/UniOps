"""Seed the finance_director post — the final sign-off on a payment.

Finance asked for one more approval after Finance Manager on the PA chain: the
Director of the Finance department, signing off the payment itself. None of the
existing step roles can carry it:

  * `director` resolves per DOCUMENT department (approval_dept_routing), so a
    Marketing PA would go to the Marketing director and a department with no
    director would skip the step — the sign-off would silently not happen.
  * `cfo` exists but is not a post; approval-api would broadcast to every user
    whose PRIMARY role is cfo and ignore anyone granted it as an additional role.

So finance_director is a company-unique POST, handled exactly like
finance_manager: approval-api broadcasts the step to whoever holds it now
(_POST_CODES), and this migration adds it to the singleton index so only one
person can.

It is ADDITIONAL-ONLY (role_defs.assignable_as_primary = false): the person who
holds it keeps their own login role (in production, the Finance department's
director) and gains this on top. Grants are read-only — what the approver
needs to open a PA and everything behind it (PR / PO / GR / invoice /
agreement) plus Portal's finance nav. Approval authority itself comes from
holding the post, not from a matrix permission.

Idempotent (ON CONFLICT DO NOTHING); seeds the permission_defs rows it
references so the role_permissions FKs resolve on a fresh database.

Revision id length: "0015_finance_director_role" is 26 characters (limit 32).
"""
from alembic import op

revision = "0015_finance_director_role"
down_revision = "0014_bank_perms"
branch_labels = None
depends_on = None

_ROLE = "finance_director"
_LABEL = "Finance Director"

_PERM_DEFS = {
    "view_pr":             ("epms", "View Pr", 0),
    "view_po":             ("epms", "View Po", 1),
    "view_gr":             ("epms", "View Gr", 2),
    "view_invoice":        ("epms", "View Invoice", 3),
    "view_pa":             ("epms", "View Pa", 4),
    "view_finance":        ("finance", "View Finance", 14),
    "view_booking":        ("booking", "View Booking", 15),
    "epms.agreement.read": ("epms", "View Agreements", 104),
}
_GRANTS = list(_PERM_DEFS)
_LOCKS = ["view_pa"]

# 0003_post_role_singleton's index, plus finance_director.
_POSTS_BEFORE = "'gm','opm','vendor_manager','finance_manager','procurement_manager'"
_POSTS_AFTER = _POSTS_BEFORE + f",'{_ROLE}'"


def _singleton_index(posts: str) -> None:
    op.execute("DROP INDEX IF EXISTS uq_user_roles_singleton_post")
    op.execute(
        "CREATE UNIQUE INDEX uq_user_roles_singleton_post ON user_roles (role_code) "
        f"WHERE role_code IN ({posts})")


def upgrade() -> None:
    for key, (module, label, sort) in _PERM_DEFS.items():
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','{module}','{label}',{sort}) "
            "ON CONFLICT (key) DO NOTHING")
    op.execute(
        "INSERT INTO role_defs(code,label,sort,is_active,assignable_as_primary) "
        f"VALUES ('{_ROLE}','{_LABEL}',852,true,false) ON CONFLICT (code) DO NOTHING")
    for key in _GRANTS:
        op.execute(
            "INSERT INTO role_permissions(role_code,permission_key) "
            f"VALUES ('{_ROLE}','{key}') ON CONFLICT DO NOTHING")
    for key in _LOCKS:
        op.execute(
            "INSERT INTO role_permission_locks(role_code,permission_key) "
            f"VALUES ('{_ROLE}','{key}') ON CONFLICT DO NOTHING")
    _singleton_index(_POSTS_AFTER)


def downgrade() -> None:
    _singleton_index(_POSTS_BEFORE)
    op.execute(f"DELETE FROM user_roles WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_permission_locks WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_permissions WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_defs WHERE code = '{_ROLE}'")
