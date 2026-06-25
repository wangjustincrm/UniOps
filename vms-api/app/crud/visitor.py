"""CRUD operations for `vms_visitors`."""
from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.visitor import Visitor
from app.schemas.visitor import VisitorCreate, VisitorUpdate


async def list_visitors(
    db: AsyncSession,
    *,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Visitor], int]:
    q = select(Visitor).order_by(Visitor.last_name, Visitor.first_name)
    if search:
        pattern = f"%{search}%"
        q = q.where(
            or_(
                Visitor.first_name.ilike(pattern),
                Visitor.last_name.ilike(pattern),
                Visitor.company_name.ilike(pattern),
                Visitor.email.ilike(pattern),
            )
        )

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = list((await db.execute(q)).scalars().all())
    return rows, total


async def get_visitor(db: AsyncSession, visitor_id: uuid.UUID) -> Visitor | None:
    return (
        await db.execute(select(Visitor).where(Visitor.id == visitor_id))
    ).scalar_one_or_none()


async def create_visitor(db: AsyncSession, payload: VisitorCreate) -> Visitor:
    row = Visitor(**payload.model_dump())
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def update_visitor(
    db: AsyncSession, visitor: Visitor, payload: VisitorUpdate
) -> Visitor:
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(visitor, k, v)
    await db.flush()
    await db.refresh(visitor)
    return visitor
