"""Staff (employee master). See docs/prd/EMPLOYEES.md.

An employee is a person on the company's books; a user is a sign-in account.
They are joined by users.erp_person_code = employees.employee_number, so there
is no foreign key and no new column on the users table shared by seven services.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, LargeBinary, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Employee(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "employees"

    employee_number: Mapped[str] = mapped_column(String(6), unique=True, index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)        # adp | non_adp
    payroll_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    work_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adp_department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    needs_department: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active", index=True)  # active | on_leave | left
    left_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Last read-back from NC. Shown, never edited.
    nc_pk: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    nc_person_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    nc_department_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    nc_department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # unknown | in_step | not_in_nc | different_department | different_status | different_code
    nc_state: Mapped[str] = mapped_column(String(25), nullable=False, default="unknown", index=True)
    nc_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmployeeHistory(UUIDPrimaryKey, Base):
    __tablename__ = "employee_history"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    import_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AdpImport(UUIDPrimaryKey, Base):
    __tablename__ = "adp_imports"
    # IMP-012: the database, not a check-then-insert, enforces one open preview.
    __table_args__ = (
        Index("uq_adp_imports_one_preview", "status", unique=True,
              postgresql_where=text("status = 'preview'")),
    )

    status: Mapped[str] = mapped_column(String(10), nullable=False, default="preview")  # preview | applied | discarded
    roster_date: Mapped[date] = mapped_column(Date, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    previewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    applied_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The groups of IMP-004. After apply this is the per-row result (IMP-013).
    groups: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # IMP-009: [{employee_id, employee_number, display_name, resolution, reason}]
    missing: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class AdpDepartmentMap(TimestampMixin, Base):
    """ADP department name -> UniOps department. '' is the empty ADP department.
    A name with no row is unmapped. choose_per_employee: never set by an import."""
    __tablename__ = "adp_department_maps"

    adp_department: Mapped[str] = mapped_column(String(255), primary_key=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    choose_per_employee: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class NcTodo(UUIDPrimaryKey, Base):
    __tablename__ = "nc_todos"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    # change_department | terminate | rehire | change_code | fix_mismatch
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open", index=True)  # open | closed
    # nc_department_code/name, person_category, old_code, new_code, description, and
    # mismatch_key: what this to-do resolves, so a to-do HR closed by hand is not
    # raised again by the next read-back.
    target: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    import_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    closed_how: Mapped[str | None] = mapped_column(String(12), nullable=True)   # auto | manual | superseded
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class NcReadbackRun(UUIDPrimaryKey, Base):
    """One full read of NC's person list. `persons` is the snapshot; the latest
    run is what the reconciliation report shows."""
    __tablename__ = "nc_readback_runs"

    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    persons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
