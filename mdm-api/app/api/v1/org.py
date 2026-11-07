"""Org chart: department boxes and reporting lines. EMPLOYEES.md §13.

This describes the company as it really is, for people to read. Approvals do not
use it, and nothing here writes to users.
"""
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.db.base import get_db
from app.models.department import Department
from app.models.employee import Employee, OrgNode, OrgUnit
from app.services import org_chart, staff

router = APIRouter(prefix="/org", tags=["org"])

ViewDep = Annotated[dict, Depends(require_permission("employee.view"))]
EditDep = Annotated[dict, Depends(require_permission("employee.edit"))]
Db = Annotated[AsyncSession, Depends(get_db)]
Relation = Literal["child", "assistant"]
Tone = Literal["teal", "violet", "outside"]


def _actor(payload: dict) -> uuid.UUID | None:
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None


async def _get(db: AsyncSession, model, box_id: uuid.UUID):
    box = await db.get(model, box_id)
    if box is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Box not found")
    return box


async def _label(db: AsyncSession, node_id: uuid.UUID | None) -> str | None:
    node = await db.get(OrgNode, node_id) if node_id else None
    if node is None:
        return None
    employee = await db.get(Employee, node.employee_id) if node.employee_id else None
    return employee.display_name if employee else node.name


async def _set_parent(db: AsyncSession, box, parent_id: uuid.UUID | None, by: uuid.UUID | None) -> None:
    """A new line is a person's answer: kept by Regenerate, no longer flagged, in the employee's history."""
    if isinstance(box, OrgNode):
        if box.employee_id and box.parent_id != parent_id:
            employee = await db.get(Employee, box.employee_id)
            staff.record(db, employee, "reports_to", await _label(db, box.parent_id), await _label(db, parent_id), by=by)
        box.source, box.review = "manual", False
    box.parent_id = parent_id


async def _lift_if_below(db: AsyncSession, model, box, parent_id: uuid.UUID | None, by) -> None:
    """Putting a box under someone below it is how a wrong first version gets turned round:
    that person comes up to the box's place first, so the chart stays a chart."""
    step = await _get(db, model, parent_id) if parent_id else None
    new_parent = step
    while step is not None:
        if step.id == box.id:
            new_parent.relation = box.relation
            await _set_parent(db, new_parent, box.parent_id, by)
            return
        step = await db.get(model, step.parent_id) if step.parent_id else None


async def _siblings(db: AsyncSession, model, parent_id: uuid.UUID | None) -> list:
    """In the order the chart shows them."""
    where = model.parent_id == parent_id if parent_id else model.parent_id.is_(None)
    if model is OrgUnit:
        rows = (await db.execute(select(OrgUnit, Department.code).outerjoin(
            Department, Department.id == OrgUnit.department_id).where(where))).all()
        rows.sort(key=lambda r: (r[0].sort, r[1] or "", r[0].name))
    else:
        rows = (await db.execute(select(OrgNode, Employee.display_name).outerjoin(
            Employee, Employee.id == OrgNode.employee_id).where(where))).all()
        rows.sort(key=lambda r: (r[0].sort, (r[1] or r[0].name or "").lower()))
    return [r[0] for r in rows]


async def _next_sort(db: AsyncSession, model, parent_id: uuid.UUID | None) -> int:
    return max((b.sort for b in await _siblings(db, model, parent_id)), default=0) + 1


class Move(BaseModel):
    target_id: uuid.UUID
    where: Literal["before", "after", "inside"]


async def _move(db: AsyncSession, model, box, body: Move, by=None) -> None:
    """Before and after put it next to the target, as its peer. Only a new parent is a new
    reporting line; reordering peers leaves it as it was."""
    target = await _get(db, model, body.target_id)
    if target.id == box.id:
        return
    parent_id = target.id if body.where == "inside" else target.parent_id
    if parent_id == box.id:                # beside its own report: that report comes up alongside it
        target.relation = box.relation
        await _set_parent(db, target, box.parent_id, by)
        parent_id = box.parent_id
    else:
        await _lift_if_below(db, model, box, parent_id, by)
    if box.parent_id != parent_id:
        await _set_parent(db, box, parent_id, by)
    if body.where == "inside":
        box.relation = "child"
        box.sort = await _next_sort(db, model, parent_id)
        return
    box.relation = target.relation
    await db.flush()
    order = [b for b in await _siblings(db, model, parent_id) if b.id != box.id]
    order.insert(order.index(target) + (body.where == "after"), box)
    for i, sibling in enumerate(order, start=1):
        sibling.sort = i


async def _close_gap(db: AsyncSession, model, box) -> None:
    """Whatever sat under a removed box moves up to where it was."""
    for child in (await db.execute(select(model).where(model.parent_id == box.id))).scalars():
        child.parent_id = box.parent_id
    await db.flush()
    await db.delete(box)


@router.get("/units")
async def list_units(db: Db, _: ViewDep):
    counts = dict((await db.execute(
        select(Employee.department_id, func.count(Employee.id))
        .where(Employee.status != "left", Employee.department_id.is_not(None))
        .group_by(Employee.department_id))).all())
    rows = (await db.execute(
        select(OrgUnit, Department.code, Employee.display_name)
        .outerjoin(Department, Department.id == OrgUnit.department_id)
        .outerjoin(Employee, Employee.id == OrgUnit.head_employee_id)
        .order_by(OrgUnit.sort, Department.code.nulls_first(), OrgUnit.name))).all()
    return {"items": [{
        "id": str(u.id), "name": u.name, "parent_id": str(u.parent_id) if u.parent_id else None,
        "relation": u.relation, "tone": u.tone,
        "department_id": str(u.department_id) if u.department_id else None, "department_code": code,
        "head_employee_id": str(u.head_employee_id) if u.head_employee_id else None,
        "head_name": head, "employees": counts.get(u.department_id, 0),
    } for u, code, head in rows]}


class UnitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: uuid.UUID | None = None
    relation: Relation = "child"


@router.post("/units", status_code=status.HTTP_201_CREATED)
async def create_unit(body: UnitCreate, db: Db, _: EditDep):
    if body.parent_id:
        await _get(db, OrgUnit, body.parent_id)
    unit = OrgUnit(id=uuid.uuid4(), name=body.name.strip(), parent_id=body.parent_id, relation=body.relation,
                   sort=await _next_sort(db, OrgUnit, body.parent_id))
    db.add(unit)
    await db.flush()
    return {"id": str(unit.id)}


class UnitUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: uuid.UUID | None = None
    head_employee_id: uuid.UUID | None = None
    relation: Relation | None = None
    tone: Tone | None = None


@router.patch("/units/{unit_id}")
async def update_unit(unit_id: uuid.UUID, body: UnitUpdate, db: Db, payload: EditDep):
    unit = await _get(db, OrgUnit, unit_id)
    sent = body.model_fields_set
    if "name" in sent and body.name:
        if unit.department_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A department is renamed under MDM → Departments")
        unit.name = body.name.strip()
    if "parent_id" in sent:
        if body.parent_id != unit.id:
            await _lift_if_below(db, OrgUnit, unit, body.parent_id, _actor(payload))
            unit.parent_id = body.parent_id
    if "head_employee_id" in sent:
        if body.head_employee_id:
            head = await db.get(Employee, body.head_employee_id)
            if head is None or head.status == "left":
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
        unit.head_employee_id = body.head_employee_id
    if "relation" in sent and body.relation:
        unit.relation = body.relation
    if "tone" in sent:
        unit.tone = body.tone
    return {"ok": True}


@router.post("/units/{unit_id}/move")
async def move_unit(unit_id: uuid.UUID, body: Move, db: Db, _: EditDep):
    await _move(db, OrgUnit, await _get(db, OrgUnit, unit_id), body)
    return {"ok": True}


@router.delete("/units/{unit_id}")
async def delete_unit(unit_id: uuid.UUID, db: Db, _: EditDep):
    unit = await _get(db, OrgUnit, unit_id)
    if unit.department_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A department box cannot be removed")
    await _close_gap(db, OrgUnit, unit)
    return {"ok": True}


@router.get("/nodes")
async def list_nodes(db: Db, _: ViewDep):
    rows = (await db.execute(
        select(OrgNode, Employee, Department.name)
        .outerjoin(Employee, Employee.id == OrgNode.employee_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .where(or_(OrgNode.employee_id.is_(None), Employee.status != "left")))).all()
    items = [{
        "id": str(n.id), "employee_id": str(e.id) if e else None, "employee_number": e.employee_number if e else None,
        "name": e.display_name if e else n.name, "title": n.title or (e.job_title if e else None),
        "job_title": e.job_title if e else None, "custom_title": n.title,
        "department": department, "adp_department": e.adp_department if e else None,
        "parent_id": str(n.parent_id) if n.parent_id else None, "relation": n.relation, "tone": n.tone,
        "source": n.source, "review": n.review, "sort": n.sort,
    } for n, e, department in rows]
    return {"items": sorted(items, key=lambda i: (i["sort"], (i["name"] or "").lower()))}


class NodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    parent_id: uuid.UUID | None = None
    relation: Relation = "child"


@router.post("/nodes", status_code=status.HTTP_201_CREATED)
async def create_node(body: NodeCreate, db: Db, _: EditDep):
    if body.parent_id:
        await _get(db, OrgNode, body.parent_id)
    node = OrgNode(id=uuid.uuid4(), name=body.name.strip(), title=(body.title or "").strip() or None,
                   parent_id=body.parent_id, relation=body.relation, source="manual",
                   sort=await _next_sort(db, OrgNode, body.parent_id))
    db.add(node)
    await db.flush()
    return {"id": str(node.id)}


class NodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    parent_id: uuid.UUID | None = None
    relation: Relation | None = None
    tone: Tone | None = None


@router.patch("/nodes/{node_id}")
async def update_node(node_id: uuid.UUID, body: NodeUpdate, db: Db, payload: EditDep):
    """Setting a line, even to the same person, is a person's answer: it is kept and no longer flagged."""
    node = await _get(db, OrgNode, node_id)
    sent = body.model_fields_set
    if "name" in sent and body.name:
        if node.employee_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "An employee's name comes from Staff")
        node.name = body.name.strip()
    if "title" in sent:
        node.title = (body.title or "").strip() or None
    if "parent_id" in sent:
        if body.parent_id != node.id:
            await _lift_if_below(db, OrgNode, node, body.parent_id, _actor(payload))
            await _set_parent(db, node, body.parent_id, _actor(payload))
    if "relation" in sent and body.relation:
        node.relation = body.relation
        node.source, node.review = "manual", False
    if "tone" in sent:
        node.tone = body.tone
    return {"ok": True}


@router.post("/nodes/{node_id}/move")
async def move_node(node_id: uuid.UUID, body: Move, db: Db, payload: EditDep):
    await _move(db, OrgNode, await _get(db, OrgNode, node_id), body, _actor(payload))
    return {"ok": True}


@router.delete("/nodes/{node_id}")
async def delete_node(node_id: uuid.UUID, db: Db, _: EditDep):
    node = await _get(db, OrgNode, node_id)
    if node.employee_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "An employee's box cannot be removed")
    await _close_gap(db, OrgNode, node)
    return {"ok": True}


@router.post("/generate")
async def generate(db: Db, _: EditDep):
    return await org_chart.generate(db)
