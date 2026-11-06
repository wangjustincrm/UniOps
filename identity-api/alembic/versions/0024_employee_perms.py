"""Access Control keys for Staff (employee master), docs/prd/EMPLOYEES.md §8.

Employee data is personal information, so unlike most master data it is not
readable by every signed-in user.

Only system_admin is granted (explicitly — effective_permissions() does not
short-circuit it, see 0013). HR staff get these in Portal → Access Control.
"""
from alembic import op

revision = "0024_employee_perms"
down_revision = "0023_freight_perms"
branch_labels = None
depends_on = None

_KEYS = (
    ("employee.view", "View Staff & NC To-dos", 150),
    ("employee.edit", "Edit Staff & Department Mapping", 151),
    ("employee.import", "Import ADP Roster", 152),
    ("employee.nc_todo", "Work NC To-dos", 153),
)


def upgrade() -> None:
    for key, label, sort in _KEYS:
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','mdm','{label.replace(chr(39), chr(39) * 2)}',{sort}) ON CONFLICT (key) DO NOTHING")
        op.execute(
            "INSERT INTO role_permissions(role_code,permission_key) "
            f"SELECT code, '{key}' FROM role_defs WHERE code = 'system_admin' "
            "ON CONFLICT DO NOTHING")


def downgrade() -> None:
    keys = ",".join(f"'{k}'" for k, _l, _s in _KEYS)
    op.execute(f"DELETE FROM role_permissions WHERE permission_key IN ({keys})")
    op.execute(f"DELETE FROM permission_defs WHERE key IN ({keys})")
