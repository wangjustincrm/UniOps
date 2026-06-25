"""CRUD operations for Unit of Measure (mdm-api owns writes)."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.part import Part
from app.models.uom import UnitOfMeasure
from app.schemas.uom import UomCreate, UomUpdate


async def get_all(db: AsyncSession, *, active_only: bool = False) -> list[UnitOfMeasure]:
    q = select(UnitOfMeasure)
    if active_only:
        q = q.where(UnitOfMeasure.is_active.is_(True))
    return list((await db.execute(q.order_by(UnitOfMeasure.code))).scalars().all())


async def get_by_id(db: AsyncSession, uom_id: uuid.UUID) -> UnitOfMeasure | None:
    return (await db.execute(
        select(UnitOfMeasure).where(UnitOfMeasure.id == uom_id)
    )).scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> UnitOfMeasure | None:
    # Case-sensitive: unit symbols keep their case (kg, m², L).
    return (await db.execute(
        select(UnitOfMeasure).where(UnitOfMeasure.code == code)
    )).scalar_one_or_none()


async def create(db: AsyncSession, payload: UomCreate) -> UnitOfMeasure:
    u = UnitOfMeasure(
        code=payload.code, name=payload.name,
        dimension=payload.dimension, is_active=payload.is_active,
    )
    db.add(u)
    await db.flush()
    await db.refresh(u)
    return u


async def update(db: AsyncSession, u: UnitOfMeasure, payload: UomUpdate) -> UnitOfMeasure:
    if payload.code is not None:
        u.code = payload.code
    if payload.name is not None:
        u.name = payload.name
    if payload.dimension is not None:
        u.dimension = payload.dimension
    if payload.is_active is not None:
        u.is_active = payload.is_active
    await db.flush()
    await db.refresh(u)
    return u


async def count_references(db: AsyncSession, code: str) -> dict:
    part_count = (await db.execute(
        select(func.count()).where(Part.unit == code)
    )).scalar_one()
    return {"parts": part_count}


async def delete(db: AsyncSession, u: UnitOfMeasure) -> None:
    await db.delete(u)
    await db.flush()
