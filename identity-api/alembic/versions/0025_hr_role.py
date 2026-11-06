"""Seed the hr role — the people who run Staff (docs/prd/EMPLOYEES.md §8).

ADDITIONAL-ONLY (role_defs.assignable_as_primary = false): HR staff keep their
own login role and gain this on top. Unlike finance_director it is not a post,
so it stays out of the singleton index and any number of people can hold it.

Grants the four employee keys from 0024 and nothing else. Creating a user and
correcting an employee number stay with system_admin.

Idempotent (ON CONFLICT DO NOTHING).
"""
from alembic import op

revision = "0025_hr_role"
down_revision = "0024_employee_perms"
branch_labels = None
depends_on = None

_ROLE = "hr"
_LABEL = "HR"
_GRANTS = ("employee.view", "employee.edit", "employee.import", "employee.nc_todo")


def upgrade() -> None:
    op.execute(
        "INSERT INTO role_defs(code,label,sort,is_active,assignable_as_primary) "
        f"VALUES ('{_ROLE}','{_LABEL}',853,true,false) ON CONFLICT (code) DO NOTHING")
    for key in _GRANTS:
        op.execute(
            "INSERT INTO role_permissions(role_code,permission_key) "
            f"VALUES ('{_ROLE}','{key}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.execute(f"DELETE FROM user_roles WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_permissions WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_defs WHERE code = '{_ROLE}'")
