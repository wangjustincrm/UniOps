"""CRUD operations for Vendor."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vendor import Vendor


def _normalize_code(code: str) -> str:
    """Normalize a vendor POID code.

    Strips leading zeros from purely-numeric codes so that '089' and '89'
    (or '0000056' and '56') resolve to the same vendor.  Non-numeric codes
    (e.g. 'ABC01') are uppercased but otherwise unchanged.
    """
    stripped = code.strip().upper()
    if stripped.isdigit():
        return str(int(stripped))
    return stripped


def _build_filter_query(
    search: str | None,
    category: str | None,
    active_only: bool,
):
    # EPMS sees suppliers only — customer-only partners (B3) are excluded
    q = select(Vendor).where(Vendor.is_supplier.is_(True))
    if search:
        term = f"%{search}%"
        q = q.where(
            Vendor.name.ilike(term)
            | Vendor.code.ilike(term)
            | Vendor.erp_id.ilike(term)
        )
    if category:
        q = q.where(Vendor.category == category)
    if active_only:
        q = q.where(Vendor.is_active.is_(True))
    return q


async def get_all(
    db: AsyncSession,
    *,
    search: str | None = None,
    category: str | None = None,
    active_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Vendor], int]:
    base = _build_filter_query(search, category, active_only)

    total_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total: int = total_result.scalar_one()

    offset = (page - 1) * page_size
    items_result = await db.execute(
        base.order_by(Vendor.code).offset(offset).limit(page_size)
    )
    return list(items_result.scalars().all()), total


async def get_all_for_export(
    db: AsyncSession,
    *,
    search: str | None = None,
    category: str | None = None,
    active_only: bool = False,
) -> list[Vendor]:
    base = _build_filter_query(search, category, active_only)
    result = await db.execute(base.order_by(Vendor.code))
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, vendor_id: uuid.UUID) -> Vendor | None:
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    return result.scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> Vendor | None:
    result = await db.execute(select(Vendor).where(Vendor.code == _normalize_code(code)))
    return result.scalar_one_or_none()


async def get_by_erp_id(db: AsyncSession, erp_id: str) -> Vendor | None:
    result = await db.execute(select(Vendor).where(Vendor.erp_id == erp_id))
    return result.scalar_one_or_none()


# NOTE: vendor master is OWNED by mdm-api (B3 / P1). EPMS no longer writes
# business_partners directly — create/update/import forward to mdm /partners
# (see api/v1/vendors.py). This module is read-only on purpose.
