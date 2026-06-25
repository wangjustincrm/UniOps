import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.vendor import Vendor


async def get_all(
    db: AsyncSession,
    *,
    search: str | None = None,
    category: str | None = None,
    active_only: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Vendor], int]:
    q = select(Vendor)
    if search:
        term = f"%{search}%"
        q = q.where(Vendor.name.ilike(term) | Vendor.code.ilike(term))
    if category:
        q = q.where(Vendor.category == category)
    if active_only:
        q = q.where(Vendor.is_active.is_(True))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(q.order_by(Vendor.name).offset((page - 1) * page_size).limit(page_size))).scalars().all())
    return items, total


async def get_by_id(db: AsyncSession, vendor_id: uuid.UUID) -> Vendor | None:
    return (await db.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> Vendor | None:
    return (await db.execute(select(Vendor).where(Vendor.code == code.upper()))).scalar_one_or_none()


async def get_by_erp_id(db: AsyncSession, erp_id: str) -> Vendor | None:
    return (await db.execute(select(Vendor).where(Vendor.erp_id == erp_id))).scalar_one_or_none()
