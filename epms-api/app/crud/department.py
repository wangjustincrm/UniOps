"""CRUD operations for Department."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.user import User
from app.schemas.department import DepartmentCreate, DepartmentUpdate


async def get_all(db: AsyncSession, active_only: bool = False) -> list[Department]:
    q = select(Department)
    if active_only:
        q = q.where(Department.is_active.is_(True))
    result = await db.execute(q.order_by(Department.code))
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, dept_id: uuid.UUID) -> Department | None:
    result = await db.execute(select(Department).where(Department.id == dept_id))
    return result.scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> Department | None:
    result = await db.execute(select(Department).where(Department.code == code.upper()))
    return result.scalar_one_or_none()


async def create(db: AsyncSession, payload: DepartmentCreate) -> Department:
    dept = Department(code=payload.code.upper(), name=payload.name, is_active=payload.is_active)
    db.add(dept)
    await db.flush()
    await db.refresh(dept)
    return dept


async def update(db: AsyncSession, dept: Department, payload: DepartmentUpdate) -> Department:
    if payload.code is not None:
        dept.code = payload.code.upper()
    if payload.name is not None:
        dept.name = payload.name
    if payload.is_active is not None:
        dept.is_active = payload.is_active
    await db.flush()
    await db.refresh(dept)
    return dept


async def count_references(db: AsyncSession, dept_id: uuid.UUID) -> dict:
    cc_count = (await db.execute(
        select(func.count()).where(CostCenter.department_id == dept_id)
    )).scalar_one()
    user_count = (await db.execute(
        select(func.count()).where(User.department_id == dept_id)
    )).scalar_one()
    return {"cost_centers": cc_count, "users": user_count}


async def delete(db: AsyncSession, dept: Department) -> None:
    await db.delete(dept)
    await db.flush()
