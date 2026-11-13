"""Compare employees with NC and close the to-dos NC has caught up with. EMPLOYEES.md §7.3.

Always a full read, never the erp_persons mirror. NC's person list holds only
enabled people, so someone terminated in NC just stops appearing (isvalid is
always 1), and a re-coded person keeps their old row in the mirror because the
mirror upserts by code. Absence is only visible in a complete list.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.department import Department
from app.models.employee import Employee, NcReadbackRun, NcTodo
from app.models.task import Task
from app.services import staff
from app.services.erp_client import ErpClient

logger = logging.getLogger(__name__)

_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
# Not in NC is a notice, not a difference: people go into NC only as needed (NCT-006).
MISMATCH_STATES = ("different_department", "different_status", "different_code")

TASK_TYPE = "resolve_nc_person_todos"
TASK_DOC_TYPE = "nc_person"
TASK_NUMBER = "NC PERSONS"
# The task is about the list, not one record, so its document id is a constant.
TASK_DOC_ID = uuid.UUID("6f1a4c52-0d0e-4a57-9d55-5e1f2c7b8a01")


async def fetch_persons(client: ErpClient | None = None) -> list[dict[str, Any]]:
    if client is not None:
        records = await client.fetch_persons(_EPOCH)
    else:
        async with ErpClient() as c:
            records = await c.fetch_persons(_EPOCH)
    return [{
        "pk": r.get("pk_psndoc"), "code": str(r.get("personcode") or ""), "name": r.get("personname"),
        "department_code": r.get("departmentcode"), "department_name": r.get("departmentname"),
    } for r in records]


def state_of(employee: Employee, person: dict | None, department_code: str | None,
             nc_department_codes: set[str]) -> str:
    left = employee.status == "left"
    if person is None:
        return "in_step" if left else "not_in_nc"
    if left:
        return "different_status"
    if person["code"] != employee.employee_number:
        return "different_code"
    # A department NC does not have (Business Development) cannot differ.
    if department_code in nc_department_codes and department_code != person["department_code"]:
        return "different_department"
    return "in_step"


def _todo_done(todo: NcTodo, employee: Employee, person: dict | None) -> bool:
    target = todo.target
    if person is None and not employee.nc_pk:
        return True                             # NC never had them: nothing to change (NCT-006)
    if todo.type == "rehire":
        return person is not None
    if todo.type == "terminate":
        return person is None
    if todo.type == "change_department":
        return person is not None and person["department_code"] == target.get("nc_department_code")
    if todo.type == "change_code":
        return person is not None and person["code"] == target.get("new_code")
    return employee.nc_state == "in_step"       # fix_mismatch


def _mismatch(state: str, employee: Employee, person: dict | None, department: Department | None) -> dict:
    if state == "different_status":
        return {"mismatch_key": "different_status:left", "person_category": staff.PERSON_CATEGORY_LEFT,
                "description": "Left in UniOps but still active in NC. Process the departure in NC HR."}
    if state == "different_code":
        return {"mismatch_key": f"different_code:{employee.employee_number}", "old_code": person["code"],
                "new_code": employee.employee_number,
                "description": f"NC has person code {person['code']}. Change it to {employee.employee_number}."}
    return {"mismatch_key": f"different_department:{department.code}",
            "nc_department_code": department.code, "nc_department_name": department.name,
            "description": f"NC has department {person['department_code']} {person['department_name'] or ''}. "
                           f"Transfer the person to {department.code} {department.name}.".replace("  ", " ")}


async def evaluate(db: AsyncSession, persons: list[dict]) -> dict:
    """Update every employee's NC state, close finished to-dos, raise the missing ones."""
    now = datetime.now(timezone.utc)
    by_pk = {p["pk"]: p for p in persons if p["pk"]}
    by_code = {p["code"]: p for p in persons}
    nc_department_codes = {p["department_code"] for p in persons if p["department_code"]}
    departments = {d.id: d for d in (await db.execute(select(Department))).scalars()}
    todos = list((await db.execute(select(NcTodo))).scalars())
    open_by_employee: dict = {}
    settled_keys: dict = {}       # to-dos HR closed by hand must not come back
    for todo in todos:
        if todo.status == "open":
            open_by_employee.setdefault(todo.employee_id, []).append(todo)
        elif todo.closed_how == "manual":
            settled_keys.setdefault(todo.employee_id, set()).add(todo.target.get("mismatch_key"))

    employees = list((await db.execute(select(Employee))).scalars())
    closed = raised = 0
    matched_pks: set[str] = set()
    for employee in employees:
        # NC's key first, so a person re-coded in NC stays the same person (NCR-002).
        person = by_pk.get(employee.nc_pk) or by_code.get(employee.employee_number)
        department = departments.get(employee.department_id)
        employee.nc_state = state_of(employee, person, department.code if department else None, nc_department_codes)
        employee.nc_checked_at = now
        employee.nc_pk = person["pk"] if person else employee.nc_pk
        employee.nc_person_code = person["code"] if person else None
        employee.nc_department_code = person["department_code"] if person else None
        employee.nc_department_name = person["department_name"] if person else None
        if person and person["pk"]:
            matched_pks.add(person["pk"])

        still_open = []
        for todo in open_by_employee.get(employee.id, []):
            if _todo_done(todo, employee, person):
                staff.close_todo(todo, "auto")
                closed += 1
            else:
                still_open.append(todo)
        if employee.nc_state in MISMATCH_STATES and not still_open:
            target = _mismatch(employee.nc_state, employee, person, department)
            if target["mismatch_key"] not in settled_keys.get(employee.id, ()):
                db.add(NcTodo(employee_id=employee.id, type="fix_mismatch", target=target))
                raised += 1

    not_in_uniops = [p for p in persons if p["pk"] not in matched_pks]
    summary = {"nc_persons": len(persons), "employees": len(employees), "closed": closed, "raised": raised,
               "not_in_uniops": len(not_in_uniops)}
    for state in ("in_step", "not_in_nc", *MISMATCH_STATES):
        summary[state] = sum(1 for e in employees if e.nc_state == state)
    await db.flush()
    return summary


async def latest_run(db: AsyncSession) -> NcReadbackRun | None:
    return (await db.execute(
        select(NcReadbackRun).order_by(NcReadbackRun.ran_at.desc()).limit(1)
    )).scalar_one_or_none()


async def record_run(db: AsyncSession, persons: list[dict]) -> dict:
    summary = await evaluate(db, persons)
    db.add(NcReadbackRun(persons=persons, summary=summary))
    return summary


async def run(db: AsyncSession, client: ErpClient | None = None) -> dict:
    summary = await record_run(db, await fetch_persons(client))
    await sync_task(db)
    return summary


async def scheduled_run() -> None:
    """After the ERP master-data sync. Failing here must not disturb the sync."""
    from app.db.base import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            logger.info("NC person read-back: %s", await run(db))
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("NC person read-back failed: %s", exc)


async def sync_task(db: AsyncSession) -> None:
    """NCT-004: one open task per role holding employee.nc_todo while anything is open."""
    open_todos = list((await db.execute(select(NcTodo).where(NcTodo.status == "open"))).scalars())
    existing = {t.assigned_role: t for t in (await db.execute(
        select(Task).where(Task.document_type == TASK_DOC_TYPE, Task.is_completed.is_(False))
    )).scalars()}
    now = datetime.now(timezone.utc)
    if not open_todos:
        for task in existing.values():
            task.is_completed = True
            task.completed_at = now
        return

    cutoff = now - timedelta(days=settings.nc_todo_overdue_days)
    overdue = sum(1 for t in open_todos if _aware(t.created_at) < cutoff)
    title = f"NC person updates pending: {len(open_todos)}"
    description = "HR changes waiting in NC. Open the NC To-do list; each item closes when NC shows the change."
    if overdue:
        title += f" ({overdue} over {settings.nc_todo_overdue_days} days)"
    roles = set((await db.execute(text(
        "select role_code from role_permissions where permission_key = 'employee.nc_todo'"
    ))).scalars()) if await _has_role_permissions(db) else set()
    for role in roles | {"system_admin"}:
        task = existing.get(role)
        if task is None:
            db.add(Task(type=TASK_TYPE, document_type=TASK_DOC_TYPE, document_id=TASK_DOC_ID,
                        document_number=TASK_NUMBER, assigned_role=role, title=title,
                        description=description, priority="urgent" if overdue else "normal"))
        else:
            task.title = title
            task.priority = "urgent" if overdue else "normal"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _has_role_permissions(db: AsyncSession) -> bool:
    """identity-api owns role_permissions; mdm-api's test database does not have it."""
    return bool((await db.execute(text("select to_regclass('role_permissions')"))).scalar())
