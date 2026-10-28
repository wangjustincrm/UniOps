"""The staff file: everything in Safety with one person's name on it (round 10).

Who may open whose, and which categories, is decided in services/staff_file —
read its docstring before changing either.
"""
from __future__ import annotations

import io
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_, select

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanReportIncident
from app.models.mirrors import Department, User
from app.services import staff_file
from uniops_authz import has_permission

router = APIRouter(prefix="/staff-file")


async def _held(db, user: dict) -> set[str]:
    me, role = _user_id(user), user.get("role", "")
    keys = {c.permission for c in staff_file.CATEGORIES} | {"ehs.settings.manage"}
    return {k for k in keys if await has_permission(db, me, role, k)}


async def _may_open(db, user: dict, person: User | None, held: set[str]) -> bool:
    if person is None:
        return False
    me = _user_id(user)
    return ("ehs.settings.manage" in held or person.id == me
            or person.supervisor_id == me)


class PersonOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None
    department: str | None
    is_active: bool


async def _person_out(db, u: User) -> PersonOut:
    dept = (await db.execute(select(Department.name).where(
        Department.id == u.department_id))).scalar() if u.department_id else None
    return PersonOut(id=u.id, full_name=u.full_name, email=u.email, department=dept,
                     is_active=u.is_active)


@router.get("/people", response_model=list[PersonOut])
async def people(db: SessionDep, user: CanReportIncident,
                 search: str | None = Query(default=None, max_length=100)):
    """Whose file this viewer may open: everybody for HSE (leavers included);
    a supervisor their direct reports; and themselves."""
    held = await _held(db, user)
    me = _user_id(user)
    q = select(User)
    if "ehs.settings.manage" not in held:
        q = q.where(or_(User.id == me, User.supervisor_id == me))
    if search and search.strip():
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.where(or_(User.full_name.ilike(f"%{term}%", escape="\\"),
                        User.email.ilike(f"%{term}%", escape="\\")))
    rows = (await db.execute(q.order_by(User.full_name).limit(50))).scalars().all()
    return [await _person_out(db, u) for u in rows]


class GroupOut(BaseModel):
    key: str
    label: str
    count: int


class RowOut(BaseModel):
    category: str
    register: str
    date: str | None
    number: str | None
    title: str | None
    path: str | None


class FileOut(BaseModel):
    person: PersonOut
    groups: list[GroupOut]
    rows: list[RowOut]
    total: int


async def _file(db, user: dict, person_id: uuid.UUID):
    held = await _held(db, user)
    person = await db.get(User, person_id)
    if not await _may_open(db, user, person, held):
        # The same answer for somebody who does not exist: the page is not a
        # way to find out who works here.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot open this person's file")
    built = await staff_file.build(db, person.id, held=held,
                                   own_file=person.id == _user_id(user))
    return person, built


@router.get("/{person_id}", response_model=FileOut)
async def open_file(person_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    person, built = await _file(db, user, person_id)
    return FileOut(person=await _person_out(db, person), **built)


@router.get("/{person_id}/export")
async def export_file(person_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    """The list as a spreadsheet, under the same rules as the page."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from app.services.http_headers import content_disposition
    person, built = await _file(db, user, person_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Staff file"
    ws.append(["Employee name", "Date", "Register type", "Number", "Title"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in built["rows"]:
        ws.append([person.full_name, (r["date"] or "")[:10], r["register"], r["number"] or "",
                   r["title"] or ""])
        # ★ Text, never a formula: a name, a number or a title can begin with "=".
        for c in ws[ws.max_row]:
            c.data_type = "s"
            c.quotePrefix = True
    for col, width in zip("ABCDE", (28, 12, 20, 18, 60)):
        ws.column_dimensions[col].width = width
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return StreamingResponse(
        out, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(
            f"staff-file-{person.full_name}", extension="xlsx", inline=False)})
