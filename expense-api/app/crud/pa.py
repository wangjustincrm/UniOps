"""PA CRUD — reads/writes the shared payment_applications table."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pa import PaymentApplication
from app.models.pa_po_link import PaPoLink


async def list_pas(
    db: AsyncSession,
    status: str | None = None,
    po_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,   # None = unrestricted (system_admin)
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[PaymentApplication], int]:
    from sqlalchemy import func, select as sa_select
    q = sa_select(PaymentApplication).where(
        PaymentApplication.pa_type == "PA-DIR"
    ).order_by(PaymentApplication.created_at.desc())
    if created_by:
        q = q.where(PaymentApplication.created_by == created_by)
    if status:
        q = q.where(PaymentApplication.status == status)
    if po_id:
        q = q.where(PaymentApplication.po_id == po_id)

    total = (await db.execute(
        sa_select(func.count()).select_from(q.subquery())
    )).scalar_one()

    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    return list(result.scalars().all()), total


async def get_by_id(db: AsyncSession, pa_id: uuid.UUID) -> PaymentApplication | None:
    result = await db.execute(
        select(PaymentApplication).where(PaymentApplication.id == pa_id)
    )
    return result.scalar_one_or_none()


async def get_by_po_id(db: AsyncSession, po_id: uuid.UUID) -> list[PaymentApplication]:
    # A PA may settle several POs; pa_po_links is the complete set (the primary
    # po_id is in it too). Matching the header column alone would leave EPMS's
    # document chain tree showing no payment on a PO that is covered second.
    result = await db.execute(
        select(PaymentApplication)
        .where(
            PaymentApplication.id.in_(
                select(PaPoLink.pa_id).where(PaPoLink.po_id == po_id)
            )
            # Union with the header column: a PA written outside epms-api's PA
            # crud has no link row, and dropping it here would show an empty
            # payment list on a PO that has in fact been paid.
            | (PaymentApplication.po_id == po_id)
        )
        .order_by(PaymentApplication.created_at.desc())
    )
    return list(result.scalars().all())


async def update_direct_pa(db: AsyncSession, pa: PaymentApplication, changes: dict) -> PaymentApplication:
    """Apply the given field changes to a PA and persist."""
    for key, value in changes.items():
        setattr(pa, key, value)
    await db.flush()
    await db.refresh(pa)
    return pa


async def count_pending() -> int:
    """Returns count of PAs pending approval (submitted + in_review)."""
    from app.db.base import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PaymentApplication)
            .where(PaymentApplication.status.in_(["submitted", "in_review"]))
        )
        return len(result.all())
