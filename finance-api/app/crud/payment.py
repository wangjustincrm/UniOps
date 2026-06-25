import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.payment import PaymentRecord
from app.models.pa import PaymentApplication
from app.schemas.payment import PaymentCreate


async def create(db: AsyncSession, payload: PaymentCreate, recorded_by: uuid.UUID) -> PaymentRecord:
    pa = (await db.execute(select(PaymentApplication).where(PaymentApplication.id == payload.pa_id))).scalar_one_or_none()
    if not pa:
        raise ValueError(f"Payment application '{payload.pa_id}' not found")

    record = PaymentRecord(
        pa_id=payload.pa_id,
        pa_number=pa.pa_number,
        vendor_id=pa.vendor_id,
        vendor_name=pa.vendor_name,
        payment_date=payload.payment_date,
        payment_method=payload.payment_method,
        reference=payload.reference,
        amount=payload.amount,
        currency=payload.currency,
        recorded_by=recorded_by,
        notes=payload.notes,
        status="completed",
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record


async def get_all(
    db: AsyncSession,
    *,
    pa_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[PaymentRecord], int]:
    q = select(PaymentRecord)
    if pa_id:
        q = q.where(PaymentRecord.pa_id == pa_id)
    if vendor_id:
        q = q.where(PaymentRecord.vendor_id == vendor_id)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(q.order_by(PaymentRecord.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all())
    return items, total


async def get_by_id(db: AsyncSession, payment_id: uuid.UUID) -> PaymentRecord | None:
    return (await db.execute(select(PaymentRecord).where(PaymentRecord.id == payment_id))).scalar_one_or_none()
