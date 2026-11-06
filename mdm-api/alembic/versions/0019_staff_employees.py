"""Staff: employees, history, ADP imports, department map, NC to-dos, read-back runs

docs/prd/EMPLOYEES.md. Additive. Nothing is seeded: the first ADP import creates
the employees, and the department map is filled in on the Staff pages.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0019_staff_employees"
down_revision = "0018_bom_line_batch_qty"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_number", sa.String(6), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("payroll_name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("work_email", sa.String(255)),
        sa.Column("job_title", sa.String(255)),
        sa.Column("adp_department", sa.String(255)),
        sa.Column("department_id", UUID(as_uuid=True)),
        sa.Column("needs_department", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("left_on", sa.Date),
        sa.Column("notes", sa.Text),
        sa.Column("nc_pk", sa.String(50)),
        sa.Column("nc_person_code", sa.String(50)),
        sa.Column("nc_department_code", sa.String(50)),
        sa.Column("nc_department_name", sa.String(255)),
        sa.Column("nc_state", sa.String(25), nullable=False, server_default="unknown"),
        sa.Column("nc_checked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )
    op.create_index("ix_employees_employee_number", "employees", ["employee_number"], unique=True)
    op.create_index("ix_employees_work_email", "employees", ["work_email"])
    op.create_index("ix_employees_department_id", "employees", ["department_id"])
    op.create_index("ix_employees_status", "employees", ["status"])
    op.create_index("ix_employees_nc_pk", "employees", ["nc_pk"])
    op.create_index("ix_employees_nc_state", "employees", ["nc_state"])

    op.create_table(
        "employee_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_id", UUID(as_uuid=True),
                  sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field", sa.String(40), nullable=False),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column("changed_by", UUID(as_uuid=True)),
        sa.Column("import_id", UUID(as_uuid=True)),
        sa.Column("reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
    )
    op.create_index("ix_employee_history_employee_id", "employee_history", ["employee_id"])

    op.create_table(
        "adp_imports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="preview"),
        sa.Column("roster_date", sa.Date, nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_bytes", sa.LargeBinary, nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=True)),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("previewed_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("applied_by", UUID(as_uuid=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("groups", JSONB, nullable=False, server_default="{}"),
        sa.Column("counts", JSONB, nullable=False, server_default="{}"),
        sa.Column("missing", JSONB, nullable=False, server_default="[]"),
    )
    op.create_index("uq_adp_imports_one_preview", "adp_imports", ["status"], unique=True,
                    postgresql_where=sa.text("status = 'preview'"))

    op.create_table(
        "adp_department_maps",
        sa.Column("adp_department", sa.String(255), primary_key=True),
        sa.Column("department_id", UUID(as_uuid=True)),
        sa.Column("choose_per_employee", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    op.create_table(
        "nc_todos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_id", UUID(as_uuid=True),
                  sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("target", JSONB, nullable=False, server_default="{}"),
        sa.Column("import_id", UUID(as_uuid=True)),
        sa.Column("created_by", UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_by", UUID(as_uuid=True)),
        sa.Column("closed_how", sa.String(12)),
        sa.Column("close_reason", sa.Text),
    )
    op.create_index("ix_nc_todos_employee_id", "nc_todos", ["employee_id"])
    op.create_index("ix_nc_todos_status", "nc_todos", ["status"])

    op.create_table(
        "nc_readback_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ran_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("persons", JSONB, nullable=False, server_default="[]"),
        sa.Column("summary", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_nc_readback_runs_ran_at", "nc_readback_runs", ["ran_at"])


def downgrade() -> None:
    for t in ("nc_readback_runs", "nc_todos", "adp_department_maps", "adp_imports",
              "employee_history", "employees"):
        op.drop_table(t)
