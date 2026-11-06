"""Give waiting employees the department their ADP department now maps to.

Saving a mapping used to reach only the next import, and an import touches a
department only for a new employee or a changed ADP department, so people imported
before the map was filled in stayed on Needs department (EMPLOYEES.md MAP-005). The
endpoint does this from now on; this is the same step for those already waiting.

Choose per employee and unmapped departments are left alone. The next NC read
compares the new departments.
"""
from alembic import op

revision = "0020_staff_apply_department_map"
down_revision = "0019_staff_employees"
branch_labels = None
depends_on = None

_WAITING = """
    e.adp_department = m.adp_department AND e.needs_department
    AND m.department_id IS NOT NULL AND NOT m.choose_per_employee
"""


def upgrade() -> None:
    op.execute(f"""
        INSERT INTO employee_history (id, employee_id, field, old_value, new_value, reason)
        SELECT gen_random_uuid(), e.id, 'department_id', e.department_id::text,
               m.department_id::text, 'Department mapping applied'
        FROM employees e JOIN adp_department_maps m ON {_WAITING}
    """)
    op.execute(f"""
        UPDATE employees e SET department_id = m.department_id, needs_department = false
        FROM adp_department_maps m WHERE {_WAITING}
    """)


def downgrade() -> None:
    pass
