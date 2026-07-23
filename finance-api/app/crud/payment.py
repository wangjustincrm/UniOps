import uuid

import sqlalchemy as sa
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.payment import PaymentRecord
from app.models.pa import PaymentApplication
from app.models.remittance import SENT, RemittanceNotification
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


def _filtered(*, pa_id=None, vendor_id=None, date_from=None, date_to=None,
              doc_kind=None, currency=None, payment_method=None, status=None,
              bank_account_id=None, batch_id=None, source=None, remittance=None,
              q=None) -> Select:
    stmt = select(PaymentRecord)
    if pa_id:
        stmt = stmt.where(PaymentRecord.pa_id == pa_id)
    if vendor_id:
        stmt = stmt.where(PaymentRecord.vendor_id == vendor_id)
    if date_from:
        stmt = stmt.where(PaymentRecord.payment_date >= date_from)
    if date_to:
        stmt = stmt.where(PaymentRecord.payment_date <= date_to)
    if doc_kind:
        stmt = stmt.where(PaymentRecord.doc_kind == doc_kind)
    if currency:
        stmt = stmt.where(PaymentRecord.currency == currency)
    if payment_method:
        stmt = stmt.where(PaymentRecord.payment_method == payment_method)
    if status:
        stmt = stmt.where(PaymentRecord.status == status)
    if bank_account_id:
        stmt = stmt.where(PaymentRecord.bank_account_id == bank_account_id)
    if batch_id:
        stmt = stmt.where(PaymentRecord.batch_id == batch_id)
    if source == "batch":
        stmt = stmt.where(PaymentRecord.batch_id.isnot(None))
    elif source == "single":
        stmt = stmt.where(PaymentRecord.batch_id.is_(None))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            PaymentRecord.doc_number.ilike(like),
            PaymentRecord.pa_number.ilike(like),
            PaymentRecord.vendor_name.ilike(like),
            PaymentRecord.reference.ilike(like),
        ))
    if remittance in ("sent", "not_sent"):
        # JSONB containment against the record id, GIN-indexed (see
        # RemittanceNotification.payment_record_ids). Written as raw SQL
        # because the right-hand side here is a correlated column, not a
        # literal, which the ORM `contains()` form doesn't support.
        notified = select(RemittanceNotification.id).where(sa.and_(
            RemittanceNotification.status == SENT,
            sa.text("payment_remittance_notifications.payment_record_ids @> "
                    "to_jsonb(payment_records.id::text)"),
        ))
        stmt = stmt.where(notified.exists() if remittance == "sent"
                          else ~notified.exists())
    return stmt


async def get_all(db: AsyncSession, *, page: int = 1, page_size: int = 50,
                  **filters) -> tuple[list[PaymentRecord], int]:
    q = _filtered(**filters)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(
        q.order_by(PaymentRecord.payment_date.desc(), PaymentRecord.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return items, total


async def summary(db: AsyncSession, **filters) -> list[dict]:
    """Per-currency count and total across the WHOLE filter, not the page."""
    base = _filtered(**filters).subquery()
    rows = (await db.execute(
        select(base.c.currency, func.count(), func.coalesce(func.sum(base.c.amount), 0))
        .group_by(base.c.currency).order_by(base.c.currency)
    )).all()
    return [{"currency": c, "count": n, "total": t} for c, n, t in rows]


async def export_rows(db: AsyncSession, **filters) -> list[PaymentRecord]:
    """Every matching record, pagination deliberately ignored."""
    q = _filtered(**filters)
    return list((await db.execute(
        q.order_by(PaymentRecord.payment_date.desc(), PaymentRecord.created_at.desc())
    )).scalars().all())


async def get_by_id(db: AsyncSession, payment_id: uuid.UUID) -> PaymentRecord | None:
    return (await db.execute(select(PaymentRecord).where(PaymentRecord.id == payment_id))).scalar_one_or_none()
