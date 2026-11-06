"""Staff (employee master), ADP import, department map, NC to-dos. EMPLOYEES.md.

Employee data is personal information: every route here needs an employee.*
permission, unlike the rest of master data.
"""
import io
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.config import settings
from app.core.deps import require_roles
from app.crud.erp import UnknownSortKey, _order
from app.db.base import get_db
from app.models.department import Department
from app.models.employee import AdpDepartmentMap, AdpImport, Employee, EmployeeHistory, NcTodo
from app.models.user import User
from app.services import adp_roster, nc_readback, staff
from app.services.erp_client import ErpError

router = APIRouter(prefix="/staff", tags=["staff"])

ViewDep = Annotated[dict, Depends(require_permission("employee.view"))]
EditDep = Annotated[dict, Depends(require_permission("employee.edit"))]
ImportDep = Annotated[dict, Depends(require_permission("employee.import"))]
TodoDep = Annotated[dict, Depends(require_permission("employee.nc_todo"))]
AdminDep = Annotated[dict, Depends(require_roles("system_admin"))]
Db = Annotated[AsyncSession, Depends(get_db)]

_NUMBER = r"^\d{6}$"
PREVIEW_MAX_AGE = timedelta(hours=24)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_DIFFERS = or_(
    func.lower(User.email) != func.coalesce(Employee.work_email, func.lower(User.email)),
    User.full_name != Employee.display_name,
    User.department_id.is_distinct_from(Employee.department_id),
    and_(Employee.status == "left", User.is_active.is_(True)),
    and_(Employee.status != "left", User.is_active.is_(False)),
)
_OPEN_TODOS = (
    select(func.count(NcTodo.id))
    .where(NcTodo.employee_id == Employee.id, NcTodo.status == "open")
    .correlate(Employee).scalar_subquery()
)
STAFF_SORTABLE = {
    "employee_number": Employee.employee_number,
    "display_name": func.lower(Employee.display_name),
    "work_email": func.lower(Employee.work_email),
    "job_title": func.lower(Employee.job_title),
    "adp_department": func.lower(Employee.adp_department),
    "department": func.lower(Department.name),
    "status": Employee.status,
    "source": Employee.source,
    "user": func.lower(User.email),
    "nc_state": Employee.nc_state,
}
TODO_SORTABLE = {
    "employee_number": Employee.employee_number,
    "display_name": func.lower(Employee.display_name),
    "type": NcTodo.type,
    "status": NcTodo.status,
    "created_at": NcTodo.created_at,
    "closed_at": NcTodo.closed_at,
}


def _actor(payload: dict) -> uuid.UUID | None:
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None


def _xlsx(title: str, header: list[str], rows: list[list], filename: str) -> Response:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return Response(buffer.getvalue(), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "Access-Control-Expose-Headers": "Content-Disposition"})


def _staff_query(*, search, status_, source, department_id, has_user, nc, differs, needs_department):
    q = (
        select(Employee, Department.name.label("department_name"), User, _OPEN_TODOS.label("open_todos"))
        .outerjoin(Department, Department.id == Employee.department_id)
        .outerjoin(User, User.erp_person_code == Employee.employee_number)
    )
    if search:
        like = f"%{search.strip()}%"
        q = q.where(or_(Employee.employee_number.ilike(like), Employee.display_name.ilike(like),
                        Employee.payroll_name.ilike(like), Employee.work_email.ilike(like)))
    if status_:
        q = q.where(Employee.status == status_)
    if source:
        q = q.where(Employee.source == source)
    if department_id:
        q = q.where(Employee.department_id == department_id)
    if has_user is not None:
        q = q.where(User.id.is_not(None) if has_user else User.id.is_(None))
    if nc == "in_step":
        q = q.where(Employee.nc_state == "in_step")
    elif nc == "todo_open":
        q = q.where(_OPEN_TODOS > 0)
    elif nc == "not_in_nc":
        q = q.where(Employee.nc_state == "not_in_nc")
    if differs:
        q = q.where(User.id.is_not(None), _DIFFERS)
    if needs_department:
        q = q.where(Employee.needs_department.is_(True))
    return q


def _staff_row(employee: Employee, department_name, user: User | None, open_todos: int) -> dict:
    return {
        "id": str(employee.id), "employee_number": employee.employee_number, "source": employee.source,
        "display_name": employee.display_name, "payroll_name": employee.payroll_name,
        "work_email": employee.work_email, "job_title": employee.job_title,
        "adp_department": employee.adp_department, "department_id": employee.department_id and str(employee.department_id),
        "department_name": department_name, "needs_department": employee.needs_department,
        "status": employee.status, "left_on": employee.left_on and employee.left_on.isoformat(),
        "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name,
                 "is_active": user.is_active} if user else None,
        "nc_state": employee.nc_state, "open_todos": open_todos,
    }


def _filters(
    search: str | None = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    source: str | None = None,
    department_id: uuid.UUID | None = None,
    has_user: bool | None = None,
    nc: Literal["in_step", "todo_open", "not_in_nc"] | None = None,
    differs: bool = False,
    needs_department: bool = False,
) -> dict:
    return dict(search=search, status_=status_, source=source, department_id=department_id,
                has_user=has_user, nc=nc, differs=differs, needs_department=needs_department)


@router.get("")
async def list_staff(
    db: Db, _: ViewDep, filters: Annotated[dict, Depends(_filters)],
    sort: str | None = None, dir: Literal["asc", "desc"] = "asc",
    page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
):
    q = _staff_query(**filters)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    try:
        q = _order(q, sort, dir, STAFF_SORTABLE, Employee.employee_number, Employee.id)
    except UnknownSortKey as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [_staff_row(*r) for r in rows], "total": total}


@router.get("/export")
async def export_staff(db: Db, _: ViewDep, filters: Annotated[dict, Depends(_filters)]):
    q = _staff_query(**filters).order_by(Employee.employee_number)
    rows = [_staff_row(*r) for r in (await db.execute(q)).all()]
    return _xlsx("Staff", [
        "Employee No.", "Name", "Payroll Name", "Email", "Job Title", "ADP Department",
        "UniOps Department", "Status", "Source", "User", "NC State",
    ], [[
        r["employee_number"], r["display_name"], r["payroll_name"], r["work_email"], r["job_title"],
        r["adp_department"], r["department_name"], r["status"], r["source"],
        r["user"]["email"] if r["user"] else None, r["nc_state"],
    ] for r in rows], f"staff-{date.today().isoformat()}.xlsx")


class EmployeeCreate(BaseModel):
    employee_number: str = Field(pattern=_NUMBER)
    display_name: str = Field(min_length=1, max_length=255)
    work_email: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)
    department_id: uuid.UUID | None = None
    notes: str | None = None


async def _email_owner(db: AsyncSession, email: str | None, exclude: uuid.UUID | None = None) -> Employee | None:
    if not email:
        return None
    q = select(Employee).where(Employee.work_email == email)
    if exclude:
        q = q.where(Employee.id != exclude)
    return (await db.execute(q.limit(1))).scalar_one_or_none()


def _clean_email(value: str | None) -> str | None:
    return (value or "").strip().lower() or None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_employee(body: EmployeeCreate, db: Db, payload: EditDep):
    """EMP-003: Non-ADP employees only; ADP employees come from the roster."""
    if (await db.execute(select(Employee.id).where(Employee.employee_number == body.employee_number))).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Employee number {body.employee_number} is in use")
    email = _clean_email(body.work_email)
    if await _email_owner(db, email):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Email {email} belongs to another employee")
    by = _actor(payload)
    employee = Employee(
        id=uuid.uuid4(), employee_number=body.employee_number, source="non_adp",
        payroll_name=body.display_name.strip(), display_name=body.display_name.strip(), work_email=email,
        job_title=body.job_title, department_id=body.department_id, notes=body.notes,
        needs_department=body.department_id is None)
    db.add(employee)
    await db.flush()
    staff.record(db, employee, "created", None, employee.employee_number, by=by)
    return {"id": str(employee.id)}


# ── ADP import ───────────────────────────────────────────────────────────────

def _import_view(imp: AdpImport, *, warnings: list[str] | None = None, full: bool = True) -> dict:
    view = {
        "id": str(imp.id), "status": imp.status, "roster_date": imp.roster_date.isoformat(),
        "file_name": imp.file_name, "uploaded_at": imp.uploaded_at, "previewed_at": imp.previewed_at,
        "applied_at": imp.applied_at, "counts": imp.counts,
        "unresolved_missing": sum(1 for m in imp.missing if not m.get("resolution")),
    }
    if full:
        view |= {"groups": imp.groups, "missing": imp.missing, "warnings": warnings or [],
                 "expired": imp.status == "preview" and _expired(imp)}
    return view


def _expired(imp: AdpImport) -> bool:
    previewed = imp.previewed_at
    previewed = previewed if previewed.tzinfo else previewed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - previewed > PREVIEW_MAX_AGE


async def _warnings(db: AsyncSession, imp: AdpImport) -> list[str]:
    last = (await db.execute(
        select(func.max(AdpImport.roster_date)).where(AdpImport.status == "applied")
    )).scalar_one_or_none()
    if imp.status == "preview" and last and imp.roster_date < last:
        return [f"The roster date {imp.roster_date.isoformat()} is earlier than the last applied import ({last.isoformat()})."]
    return []


async def _get_import(db: AsyncSession, import_id: uuid.UUID) -> AdpImport:
    imp = await db.get(AdpImport, import_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    return imp


def _need_preview(imp: AdpImport) -> None:
    if imp.status != "preview":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This import is already {imp.status}")


@router.post("/imports", status_code=status.HTTP_201_CREATED)
async def upload_roster(db: Db, payload: ImportDep, file: UploadFile = File(...),
                        roster_date: date = Form(default_factory=date.today)):
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only .xlsx files are accepted")
    if (await db.execute(select(AdpImport.id).where(AdpImport.status == "preview"))).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Another import is waiting. Apply or discard it first.")
    data = await file.read()
    try:
        groups, counts = await staff.build_preview(db, data)
    except adp_roster.RosterFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    imp = AdpImport(status="preview", roster_date=roster_date, file_name=file.filename, file_bytes=data,
                    uploaded_by=_actor(payload), groups=groups, counts=counts)
    db.add(imp)
    try:
        await db.flush()
    except IntegrityError as exc:      # two uploads at once: the unique index decides
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Another import is waiting. Apply or discard it first.") from exc
    return _import_view(imp, warnings=await _warnings(db, imp))


@router.get("/imports")
async def list_imports(db: Db, _: ImportDep):
    q = select(AdpImport).where(AdpImport.status == "applied").order_by(AdpImport.applied_at.desc())
    return {"items": [_import_view(i, full=False) for i in (await db.execute(q)).scalars()]}


@router.get("/imports/current")
async def current_import(db: Db, _: ImportDep):
    imp = (await db.execute(select(AdpImport).where(AdpImport.status == "preview"))).scalar_one_or_none()
    return _import_view(imp, warnings=await _warnings(db, imp)) if imp else None


@router.get("/imports/{import_id}")
async def get_import(import_id: uuid.UUID, db: Db, _: ImportDep):
    imp = await _get_import(db, import_id)
    view = _import_view(imp, warnings=await _warnings(db, imp))
    view["todos"] = (await db.execute(
        select(func.count(NcTodo.id)).where(NcTodo.import_id == imp.id))).scalar_one()
    return view


@router.get("/imports/{import_id}/file")
async def download_import_file(import_id: uuid.UUID, db: Db, _: ImportDep):
    imp = await _get_import(db, import_id)
    return Response(imp.file_bytes, media_type=XLSX, headers={
        "Content-Disposition": f'attachment; filename="{imp.file_name}"',
        "Access-Control-Expose-Headers": "Content-Disposition"})


@router.post("/imports/{import_id}/regenerate")
async def regenerate_preview(import_id: uuid.UUID, db: Db, _: ImportDep):
    imp = await _get_import(db, import_id)
    _need_preview(imp)
    imp.groups, imp.counts = await staff.build_preview(db, imp.file_bytes)
    imp.previewed_at = datetime.now(timezone.utc)
    return _import_view(imp, warnings=await _warnings(db, imp))


@router.post("/imports/{import_id}/discard")
async def discard_import(import_id: uuid.UUID, db: Db, _: ImportDep):
    imp = await _get_import(db, import_id)
    _need_preview(imp)
    imp.status = "discarded"
    return {"ok": True}


@router.post("/imports/{import_id}/apply")
async def apply_import(import_id: uuid.UUID, db: Db, payload: ImportDep):
    imp = await _get_import(db, import_id)
    _need_preview(imp)
    if _expired(imp):
        raise HTTPException(status.HTTP_409_CONFLICT, "This preview is over 24 hours old. Regenerate it first.")
    # Read NC first, so the changes below see who is in NC. If NC is down the import
    # still goes through; the next read-back closes what it can.
    try:
        persons = await nc_readback.fetch_persons()
    except ErpError:
        persons = None
    await staff.apply_import(db, imp, by=_actor(payload))
    await db.flush()
    if persons is not None:
        await nc_readback.record_run(db, persons)
    await nc_readback.sync_task(db)
    view = _import_view(imp)
    view["todos"] = (await db.execute(
        select(func.count(NcTodo.id)).where(NcTodo.import_id == imp.id))).scalar_one()
    view["nc_checked"] = persons is not None
    return view


class MissingDecision(BaseModel):
    action: Literal["left", "keep"]
    left_on: date | None = None
    reason: str | None = None


@router.post("/imports/{import_id}/missing/{employee_id}")
async def resolve_missing(import_id: uuid.UUID, employee_id: uuid.UUID, body: MissingDecision,
                          db: Db, payload: ImportDep):
    """IMP-009: nobody is marked Left by an import; each person is decided here."""
    imp = await _get_import(db, import_id)
    if not any(m["employee_id"] == str(employee_id) for m in imp.missing):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This employee is not on the missing list")
    if body.action == "left" and not body.left_on:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter the date they left")
    if body.action == "keep" and not (body.reason or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a reason for keeping them")
    if body.action == "left":
        await staff.mark_left(db, await _get_employee(db, employee_id), body.left_on, by=_actor(payload))
        await nc_readback.sync_task(db)
    imp.missing = [
        {**m, "resolution": body.action, "reason": body.reason} if m["employee_id"] == str(employee_id) else m
        for m in imp.missing
    ]
    return _import_view(imp)


# ── Department map ───────────────────────────────────────────────────────────

@router.get("/department-map")
async def department_map(db: Db, _: ViewDep):
    """Every ADP department known: mapped, or seen on an employee. '' is the empty one."""
    maps = {m.adp_department: m for m in (await db.execute(select(AdpDepartmentMap))).scalars()}
    name = func.coalesce(Employee.adp_department, "")
    seen = dict((await db.execute(
        select(name, func.count(Employee.id))
        .where(Employee.source == "adp", Employee.status != "left").group_by(name))).all())
    current = (await db.execute(select(AdpImport).where(AdpImport.status == "preview"))).scalar_one_or_none()
    pending = set(current.groups.get("unmapped", [])) if current else set()
    names = sorted(set(maps) | set(seen) | pending)
    return {"items": [{
        "adp_department": n, "employees": seen.get(n, 0),
        "department_id": str(maps[n].department_id) if n in maps and maps[n].department_id else None,
        "choose_per_employee": maps[n].choose_per_employee if n in maps else False,
        "mapped": n in maps,
    } for n in names]}


class DepartmentMapUpdate(BaseModel):
    adp_department: str = Field(max_length=255)
    department_id: uuid.UUID | None = None
    choose_per_employee: bool = False


@router.put("/department-map")
async def set_department_map(body: DepartmentMapUpdate, db: Db, payload: EditDep):
    if body.choose_per_employee == (body.department_id is not None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Pick a department, or Choose per employee")
    row = await db.get(AdpDepartmentMap, body.adp_department)
    if row is None:
        row = AdpDepartmentMap(adp_department=body.adp_department)
        db.add(row)
    row.department_id = body.department_id
    row.choose_per_employee = body.choose_per_employee
    applied = 0
    if body.department_id is not None:
        applied = await staff.apply_department_map(db, body.adp_department, body.department_id, by=_actor(payload))
    if applied:
        await db.flush()
        latest = await nc_readback.latest_run(db)
        if latest:
            await nc_readback.evaluate(db, latest.persons)
            await nc_readback.sync_task(db)
    return {"ok": True, "applied": applied}


# ── NC to-dos and read-back ──────────────────────────────────────────────────

def _todo_query(status_, type_, search):
    q = (
        select(NcTodo, Employee.employee_number, Employee.display_name)
        .join(Employee, Employee.id == NcTodo.employee_id)
    )
    if status_:
        q = q.where(NcTodo.status == status_)
    if type_:
        q = q.where(NcTodo.type == type_)
    if search:
        like = f"%{search.strip()}%"
        q = q.where(or_(Employee.employee_number.ilike(like), Employee.display_name.ilike(like)))
    return q


def _todo_row(todo: NcTodo, number: str, name: str) -> dict:
    created = todo.created_at if todo.created_at.tzinfo else todo.created_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - created
    return {
        "id": str(todo.id), "employee_id": str(todo.employee_id), "employee_number": number,
        "display_name": name, "type": todo.type, "status": todo.status, "target": todo.target,
        "from_import": todo.import_id is not None, "created_at": todo.created_at,
        "closed_at": todo.closed_at, "closed_how": todo.closed_how, "close_reason": todo.close_reason,
        "overdue": todo.status == "open" and age.days >= settings.nc_todo_overdue_days,
    }


@router.get("/nc-todos")
async def list_todos(
    db: Db, _: ViewDep,
    status_: Annotated[str | None, Query(alias="status")] = None,
    type_: Annotated[str | None, Query(alias="type")] = None,
    search: str | None = None,
    sort: str | None = None, dir: Literal["asc", "desc"] = "asc",
    page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
):
    q = _todo_query(status_, type_, search)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    try:
        q = _order(q, sort, dir, TODO_SORTABLE, NcTodo.created_at, NcTodo.id)
    except UnknownSortKey as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [_todo_row(*r) for r in rows], "total": total}


@router.get("/nc-todos/export")
async def export_todos(
    db: Db, _: ViewDep,
    status_: Annotated[str | None, Query(alias="status")] = None,
    type_: Annotated[str | None, Query(alias="type")] = None,
    search: str | None = None,
):
    q = _todo_query(status_, type_, search).order_by(Employee.employee_number)
    rows = [_todo_row(*r) for r in (await db.execute(q)).all()]
    return _xlsx("NC To-do", [
        "Employee No.", "Name", "To-do", "NC Department", "Person Category", "Old Code", "New Code",
        "Details", "Status", "Created",
    ], [[
        r["employee_number"], r["display_name"], r["type"],
        " ".join(filter(None, [r["target"].get("nc_department_code"), r["target"].get("nc_department_name")])),
        r["target"].get("person_category"), r["target"].get("old_code"), r["target"].get("new_code"),
        r["target"].get("description"), r["status"], r["created_at"].date().isoformat(),
    ] for r in rows], f"nc-todo-{date.today().isoformat()}.xlsx")


class TodoClose(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1)


@router.post("/nc-todos/close")
async def close_todos(body: TodoClose, db: Db, payload: TodoDep):
    """NCT-002: by hand, with a reason. Many at once, because the first roster
    can leave a long list that HR rules out with one reason."""
    todos = (await db.execute(
        select(NcTodo).where(NcTodo.id.in_(body.ids), NcTodo.status == "open"))).scalars().all()
    for todo in todos:
        staff.close_todo(todo, "manual", by=_actor(payload), reason=body.reason.strip())
    await db.flush()
    await nc_readback.sync_task(db)
    return {"closed": len(todos)}


@router.post("/nc/check")
async def check_nc(db: Db, _: TodoDep):
    try:
        return await nc_readback.run(db)
    except ErpError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not read NC: {exc}") from exc


@router.get("/nc/report")
async def nc_report(db: Db, _: ViewDep):
    """NCR-004: every difference, not only open to-dos."""
    run = await nc_readback.latest_run(db)
    rows = (await db.execute(
        select(Employee, Department.name, Department.code)
        .outerjoin(Department, Department.id == Employee.department_id)
        .where(Employee.nc_state.in_((*nc_readback.MISMATCH_STATES, "not_in_nc")))
        .order_by(Employee.nc_state, Employee.employee_number))).all()
    known = {e for e in (await db.execute(select(Employee.nc_pk))).scalars() if e}
    return {
        "ran_at": run.ran_at if run else None, "summary": run.summary if run else None,
        "differences": [{
            "employee_id": str(e.id), "employee_number": e.employee_number, "display_name": e.display_name,
            "status": e.status, "nc_state": e.nc_state, "department": name, "department_code": code,
            "nc_person_code": e.nc_person_code, "nc_department_code": e.nc_department_code,
            "nc_department_name": e.nc_department_name,
        } for e, name, code in rows if e.nc_state != "not_in_nc"],
        "not_in_nc": [{
            "employee_id": str(e.id), "employee_number": e.employee_number, "display_name": e.display_name,
            "job_title": e.job_title, "department": name, "department_code": code,
        } for e, name, code in rows if e.nc_state == "not_in_nc"],
        "not_in_uniops": [p for p in (run.persons if run else []) if p["pk"] not in known],
    }


@router.get("/unlinked-users")
async def unlinked_users(db: Db, _: ViewDep):
    """UGEN-007: active users with no employee, for an administrator to classify."""
    q = (
        select(User.id, User.email, User.full_name, User.erp_person_code)
        .outerjoin(Employee, Employee.employee_number == User.erp_person_code)
        .where(User.is_active.is_(True), Employee.id.is_(None))
        .order_by(func.lower(User.full_name))
    )
    return {"items": [{"id": str(r.id), "email": r.email, "full_name": r.full_name,
                       "erp_person_code": r.erp_person_code} for r in (await db.execute(q)).all()]}


# ── One employee (keep last: /{employee_id} would swallow the routes above) ──

async def _get_employee(db: AsyncSession, employee_id: uuid.UUID) -> Employee:
    employee = await db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
    return employee


def differences(employee: Employee, user: User, department_name, user_department_name) -> list[dict]:
    """UGEN-008. `field` and `value` are what Apply to user sends to the user update."""
    diffs = []
    if employee.display_name != user.full_name:
        diffs.append({"field": "full_name", "label": "Name", "employee": employee.display_name,
                      "user": user.full_name, "value": employee.display_name})
    if employee.work_email and employee.work_email != user.email.lower():
        diffs.append({"field": "email", "label": "Email", "employee": employee.work_email,
                      "user": user.email, "value": employee.work_email})
    if employee.department_id != user.department_id:
        diffs.append({"field": "department_id", "label": "Department", "employee": department_name,
                      "user": user_department_name,
                      "value": employee.department_id and str(employee.department_id)})
    if employee.status == "left" and user.is_active:
        diffs.append({"field": "is_active", "label": "Active", "employee": "Left", "user": "Active", "value": False})
    if employee.status != "left" and not user.is_active:
        diffs.append({"field": "is_active", "label": "Active", "employee": employee.status,
                      "user": "Inactive", "value": True})
    return diffs


@router.get("/{employee_id}")
async def get_employee(employee_id: uuid.UUID, db: Db, _: ViewDep):
    employee = await _get_employee(db, employee_id)
    user = (await db.execute(
        select(User).where(User.erp_person_code == employee.employee_number))).scalar_one_or_none()
    names = {d.id: d.name for d in (await db.execute(select(Department))).scalars()}
    department_name = names.get(employee.department_id)
    open_todos = await staff.open_todos(db, employee.id)
    history = (await db.execute(
        select(EmployeeHistory, User.full_name)
        .outerjoin(User, User.id == EmployeeHistory.changed_by)
        .where(EmployeeHistory.employee_id == employee.id)
        .order_by(EmployeeHistory.created_at.desc()))).all()
    return {
        **_staff_row(employee, department_name, user, len(open_todos)),
        "notes": employee.notes,
        "nc": {"person_code": employee.nc_person_code, "department_code": employee.nc_department_code,
               "department_name": employee.nc_department_name, "checked_at": employee.nc_checked_at},
        "todos": [_todo_row(t, employee.employee_number, employee.display_name) for t in open_todos],
        "differences": differences(employee, user, department_name, names.get(user.department_id)) if user else [],
        "history": [{
            "field": h.field, "old_value": h.old_value, "new_value": h.new_value, "reason": h.reason,
            "from_import": h.import_id is not None, "by": by, "at": h.created_at,
        } for h, by in history],
    }


class EmployeeUpdate(BaseModel):
    department_id: uuid.UUID | None = None
    notes: str | None = None
    # Non-ADP only (EMP-004): the next import overwrites these on an ADP employee.
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    work_email: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)
    status: Literal["active", "on_leave"] | None = None


@router.patch("/{employee_id}")
async def update_employee(employee_id: uuid.UUID, body: EmployeeUpdate, db: Db, payload: EditDep):
    employee = await _get_employee(db, employee_id)
    sent = body.model_fields_set
    by = _actor(payload)
    adp_only = sent & {"display_name", "work_email", "job_title", "status"}
    if employee.source == "adp" and adp_only:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"ADP provides {', '.join(sorted(adp_only))} for this employee")
    if "status" in sent and employee.status == "left":
        raise HTTPException(status.HTTP_409_CONFLICT, "Use Reinstate for an employee who has left")
    if "work_email" in sent:
        email = _clean_email(body.work_email)
        if await _email_owner(db, email, exclude=employee.id):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Email {email} belongs to another employee")
        staff.set_field(db, employee, "work_email", email, by=by)
    if "display_name" in sent and body.display_name:
        staff.set_field(db, employee, "display_name", body.display_name.strip(), by=by)
        staff.set_field(db, employee, "payroll_name", body.display_name.strip(), by=by)
    for field in ("job_title", "status", "notes"):
        if field in sent:
            staff.set_field(db, employee, field, getattr(body, field), by=by)
    if "department_id" in sent:
        changed = staff.set_field(db, employee, "department_id", body.department_id, by=by)
        employee.needs_department = body.department_id is None
        if changed:
            await staff.raise_change_department(db, employee, by=by)
            await nc_readback.sync_task(db)
    return {"ok": True}


class MarkLeft(BaseModel):
    left_on: date


@router.post("/{employee_id}/leave")
async def mark_left(employee_id: uuid.UUID, body: MarkLeft, db: Db, payload: EditDep):
    employee = await _get_employee(db, employee_id)
    if employee.status == "left":
        raise HTTPException(status.HTTP_409_CONFLICT, "This employee has already left")
    await staff.mark_left(db, employee, body.left_on, by=_actor(payload))
    await nc_readback.sync_task(db)
    return {"ok": True}


@router.post("/{employee_id}/reinstate")
async def reinstate(employee_id: uuid.UUID, db: Db, payload: EditDep):
    employee = await _get_employee(db, employee_id)
    if employee.status != "left":
        raise HTTPException(status.HTTP_409_CONFLICT, "This employee has not left")
    await staff.reinstate(db, employee, by=_actor(payload))
    await nc_readback.sync_task(db)
    return {"ok": True}


class NumberCorrection(BaseModel):
    new_number: str = Field(pattern=_NUMBER)
    reason: str = Field(min_length=1)


@router.post("/{employee_id}/correct-number")
async def correct_number(employee_id: uuid.UUID, body: NumberCorrection, db: Db, payload: AdminDep):
    employee = await _get_employee(db, employee_id)
    if body.new_number == employee.employee_number:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That is already the employee number")
    taken = (await db.execute(select(Employee.id).where(Employee.employee_number == body.new_number))).first() \
        or (await db.execute(select(User.id).where(User.erp_person_code == body.new_number))).first()
    if taken:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Number {body.new_number} is in use")
    await staff.correct_number(db, employee, body.new_number, body.reason.strip(), by=_actor(payload))
    await nc_readback.sync_task(db)
    return {"ok": True}


@router.post("/{employee_id}/convert-to-adp")
async def convert_to_adp(employee_id: uuid.UUID, db: Db, payload: ImportDep):
    """IMP-008: a Non-ADP employee found on the roster. The next import maintains them."""
    employee = await _get_employee(db, employee_id)
    staff.set_field(db, employee, "source", "adp", by=_actor(payload))
    return {"ok": True}
