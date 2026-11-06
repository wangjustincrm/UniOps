"""Employee writes, NC to-dos and the import apply. EMPLOYEES.md §3, §4, §7.

Name and email changes never create to-dos (§7.1). Nothing here changes a user,
except correcting an employee number, which is the link itself (EMP-006).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.employee import AdpDepartmentMap, AdpImport, Employee, EmployeeHistory, NcTodo
from app.models.user import User
from app.services import adp_roster

PERSON_CATEGORY_REGULAR = "0101 Regular employee"
PERSON_CATEGORY_LEFT = "0201 Left"
# A later to-do of the second type makes an open one of the first pointless.
_CANCELS = {"rehire": ("terminate",), "terminate": ("rehire",)}


def employee_dict(e: Employee) -> dict:
    return {
        "id": e.id, "employee_number": e.employee_number, "source": e.source,
        "payroll_name": e.payroll_name, "display_name": e.display_name, "work_email": e.work_email,
        "job_title": e.job_title, "adp_department": e.adp_department, "status": e.status,
    }


def record(db: AsyncSession, employee: Employee, field: str, old, new, *, by: uuid.UUID | None,
           import_id: uuid.UUID | None = None, reason: str | None = None) -> None:
    db.add(EmployeeHistory(
        employee_id=employee.id, field=field,
        old_value=None if old is None else str(old), new_value=None if new is None else str(new),
        changed_by=by, import_id=import_id, reason=reason))


def set_field(db: AsyncSession, employee: Employee, field: str, value, *, by, import_id=None, reason=None) -> bool:
    old = getattr(employee, field)
    if old == value:
        return False
    setattr(employee, field, value)
    record(db, employee, field, old, value, by=by, import_id=import_id, reason=reason)
    return True


async def open_todos(db: AsyncSession, employee_id: uuid.UUID) -> list[NcTodo]:
    return list((await db.execute(
        select(NcTodo).where(NcTodo.employee_id == employee_id, NcTodo.status == "open")
    )).scalars())


def close_todo(todo: NcTodo, how: str, *, by: uuid.UUID | None = None, reason: str | None = None) -> None:
    todo.status = "closed"
    todo.closed_at = datetime.now(timezone.utc)
    todo.closed_how = how
    todo.closed_by = by
    todo.close_reason = reason


async def raise_todo(db: AsyncSession, employee: Employee, type_: str, target: dict, *,
                     by: uuid.UUID | None, import_id: uuid.UUID | None = None) -> NcTodo:
    """NCT-003: the new to-do supersedes open ones of the same type."""
    replaced = (type_, *_CANCELS.get(type_, ()))
    for todo in await open_todos(db, employee.id):
        if todo.type in replaced:
            close_todo(todo, "superseded")
    todo = NcTodo(employee_id=employee.id, type=type_, target=target, created_by=by, import_id=import_id)
    db.add(todo)
    return todo


async def department_target(db: AsyncSession, department_id: uuid.UUID | None) -> dict:
    """UniOps departments carry NC's department code as their own code (MAP-004)."""
    department = await db.get(Department, department_id) if department_id else None
    if department is None:
        return {}
    return {"nc_department_code": department.code, "nc_department_name": department.name}


def known_to_nc(employee: Employee) -> bool:
    """NCT-006: not everyone belongs in NC, so nobody is asked to change a person
    NC has never had. Before the first read nothing is known, and a to-do is raised;
    the read-back closes it if NC turns out not to have them."""
    return bool(employee.nc_pk) or employee.nc_state == "unknown"


async def raise_change_department(db, employee, *, by, import_id=None) -> None:
    target = await department_target(db, employee.department_id)
    if target and known_to_nc(employee):
        await raise_todo(db, employee, "change_department", {
            **target, "mismatch_key": f"different_department:{target['nc_department_code']}",
        }, by=by, import_id=import_id)


async def mark_left(db: AsyncSession, employee: Employee, left_on: date, *, by, reason=None) -> None:
    set_field(db, employee, "status", "left", by=by, reason=reason)
    set_field(db, employee, "left_on", left_on, by=by, reason=reason)
    if known_to_nc(employee):
        await raise_todo(db, employee, "terminate", {
            "person_category": PERSON_CATEGORY_LEFT, "mismatch_key": "different_status:left",
        }, by=by)


async def reinstate(db: AsyncSession, employee: Employee, *, by, import_id=None, status="active") -> None:
    set_field(db, employee, "status", status, by=by, import_id=import_id)
    set_field(db, employee, "left_on", None, by=by, import_id=import_id)
    if known_to_nc(employee):
        await raise_todo(db, employee, "rehire", {
            **await department_target(db, employee.department_id),
            "person_category": PERSON_CATEGORY_REGULAR, "mismatch_key": "not_in_nc",
        }, by=by, import_id=import_id)


async def correct_number(db: AsyncSession, employee: Employee, new_number: str, reason: str, *, by) -> None:
    """EMP-006. The user's ERP person code is the link, so it moves in the same step."""
    old = employee.employee_number
    user = (await db.execute(select(User).where(User.erp_person_code == old))).scalar_one_or_none()
    set_field(db, employee, "employee_number", new_number, by=by, reason=reason)
    if user is not None:
        user.erp_person_code = new_number
    if known_to_nc(employee):
        await raise_todo(db, employee, "change_code", {
            "old_code": old, "new_code": new_number, "mismatch_key": f"different_code:{new_number}",
        }, by=by)


async def load_classify_inputs(db: AsyncSession) -> tuple[list[dict], dict, dict]:
    employees = [employee_dict(e) for e in (await db.execute(select(Employee))).scalars()]
    users = (await db.execute(select(User.email, User.erp_person_code))).all()
    department_map = {
        m.adp_department: {"department_id": m.department_id, "choose_per_employee": m.choose_per_employee}
        for m in (await db.execute(select(AdpDepartmentMap))).scalars()
    }
    return employees, {u.email.lower(): u.erp_person_code for u in users}, department_map


async def build_preview(db: AsyncSession, data: bytes) -> tuple[dict, dict]:
    rows = adp_roster.parse_roster(data)
    groups = adp_roster.classify(rows, *await load_classify_inputs(db))
    return groups, adp_roster.counts(groups)


def _mapped_department(department_map: dict, adp_department: str | None):
    """(department_id or None, needs_department). MAP-002 / MAP-003."""
    mapping = department_map.get(adp_department or "")
    if mapping is None:
        return None, True
    if mapping["choose_per_employee"] or mapping["department_id"] is None:
        return None, False
    return mapping["department_id"], False


async def apply_department_map(db: AsyncSession, adp_department: str, department_id: uuid.UUID, *, by) -> int:
    """MAP-005: an import will not revisit these people, so saving the mapping has to."""
    waiting = (await db.execute(select(Employee).where(
        Employee.adp_department == adp_department, Employee.needs_department.is_(True)))).scalars().all()
    for employee in waiting:
        set_field(db, employee, "department_id", department_id, by=by, reason="Department mapping applied")
        employee.needs_department = False
    return len(waiting)


async def apply_import(db: AsyncSession, imp: AdpImport, *, by: uuid.UUID | None) -> dict:
    """IMP-005. Classifies again from the stored file, so what is written matches
    the database now, not when the preview was taken. The caller's transaction
    makes it all-or-nothing. Never touches a user (IMP-007)."""
    rows = {r.employee_number: r for r in adp_roster.parse_roster(imp.file_bytes) if r.employee_number}
    employees, user_codes, department_map = await load_classify_inputs(db)
    groups = adp_roster.classify(adp_roster.parse_roster(imp.file_bytes), employees, user_codes, department_map)

    for item in groups["new"]:
        row = rows[item["employee_number"]]
        department_id, needs_department = _mapped_department(department_map, row.adp_department)
        employee = Employee(
            id=uuid.uuid4(), employee_number=row.employee_number, source="adp",
            payroll_name=row.payroll_name, display_name=row.display_name, work_email=row.work_email,
            job_title=row.job_title, adp_department=row.adp_department, status=row.status,
            department_id=department_id, needs_department=needs_department or department_id is None)
        db.add(employee)
        await db.flush()
        record(db, employee, "created", None, row.employee_number, by=by, import_id=imp.id)

    for item in groups["changed"]:
        row = rows[item["employee_number"]]
        employee = await db.get(Employee, uuid.UUID(item["employee_id"]))
        was_left = employee.status == "left"
        department_changed = employee.adp_department != row.adp_department
        for f in adp_roster.ADP_FIELDS:
            if f != "status":
                set_field(db, employee, f, getattr(row, f), by=by, import_id=imp.id)
        if was_left:
            await reinstate(db, employee, by=by, import_id=imp.id, status=row.status)
        else:
            set_field(db, employee, "status", row.status, by=by, import_id=imp.id)
        if department_changed:
            department_id, needs_department = _mapped_department(department_map, row.adp_department)
            employee.needs_department = needs_department
            if department_id and set_field(db, employee, "department_id", department_id, by=by, import_id=imp.id):
                await raise_change_department(db, employee, by=by, import_id=imp.id)

    imp.groups = groups
    imp.counts = adp_roster.counts(groups)
    imp.missing = [{**m, "resolution": None, "reason": None} for m in groups["missing"]]
    imp.status = "applied"
    imp.applied_by = by
    imp.applied_at = datetime.now(timezone.utc)
    return imp.counts
