import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.part import Part


async def get_all(
    db: AsyncSession,
    *,
    search: str | None = None,
    category: str | None = None,
    active_only: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Part], int]:
    q = select(Part)
    if search:
        term = f"%{search}%"
        q = q.where(Part.name.ilike(term) | Part.code.ilike(term))
    if category:
        q = q.where(Part.category == category)
    if active_only:
        q = q.where(Part.is_active.is_(True))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(q.order_by(Part.code).offset((page - 1) * page_size).limit(page_size))).scalars().all())
    return items, total


async def get_by_id(db: AsyncSession, part_id: uuid.UUID) -> Part | None:
    return (await db.execute(select(Part).where(Part.id == part_id))).scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> Part | None:
    return (await db.execute(select(Part).where(Part.code == code.upper()))).scalar_one_or_none()
