import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.pa import PaymentApplication


async def get_payables(
    db: AsyncSession,
    *,
    status: str | None = "approved",
    vendor_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[PaymentApplication], int]:
    q = select(PaymentApplication)
    if status:
        q = q.where(PaymentApplication.status == status)
    if vendor_id:
        q = q.where(PaymentApplication.vendor_id == vendor_id)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(q.order_by(PaymentApplication.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all())
    return items, total


async def get_payable_by_id(db: AsyncSession, pa_id: uuid.UUID) -> PaymentApplication | None:
    return (await db.execute(select(PaymentApplication).where(PaymentApplication.id == pa_id))).scalar_one_or_none()
