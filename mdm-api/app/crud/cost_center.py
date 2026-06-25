"""CRUD operations for CostCenter (mdm-api owns writes).

PR references are checked via raw SQL since PurchaseRequest lives in epms-api,
not mdm-api. The two services share a Postgres database so the cross-table
count is safe and consistent.
"""
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_center import CostCenter
from app.schemas.cost_center import CostCenterCreate, CostCenterUpdate


async def get_all(
    db: AsyncSession,
    *,
    department_id: uuid.UUID | None = None,
    active_only: bool = False,
) -> list[CostCenter]:
    q = select(CostCenter)
    if department_id:
        q = q.where(CostCenter.department_id == department_id)
    if active_only:
        q = q.where(CostCenter.is_active.is_(True))
    return list((await db.execute(q.order_by(CostCenter.code))).scalars().all())


async def get_by_id(db: AsyncSession, cc_id: uuid.UUID) -> CostCenter | None:
    return (await db.execute(select(CostCenter).where(CostCenter.id == cc_id))).scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> CostCenter | None:
    return (await db.execute(select(CostCenter).where(CostCenter.code == code.upper()))).scalar_one_or_none()


async def create(db: AsyncSession, payload: CostCenterCreate) -> CostCenter:
    cc = CostCenter(
        code=payload.code.upper(),
        name=payload.name,
        department_id=payload.department_id,
        is_active=payload.is_active,
    )
    db.add(cc)
    await db.flush()
    await db.refresh(cc)
    return cc


async def update(db: AsyncSession, cc: CostCenter, payload: CostCenterUpdate) -> CostCenter:
    if payload.code is not None:
        cc.code = payload.code.upper()
    if payload.name is not None:
        cc.name = payload.name
    if payload.department_id is not None:
        cc.department_id = payload.department_id
    if payload.is_active is not None:
        cc.is_active = payload.is_active
    await db.flush()
    await db.refresh(cc)
    return cc


async def count_references(db: AsyncSession, cc_id: uuid.UUID) -> int:
    """Count PR rows pointing at this cost_center via the shared DB.

    Uses raw SQL because the PurchaseRequest model lives in epms-api. Returns
    0 if the purchase_requests table is absent (e.g. mdm-api running against
    a standalone test DB).
    """
    try:
        row = await db.execute(
            text("SELECT count(*) FROM purchase_requests WHERE cost_center_id = :cc_id"),
            {"cc_id": str(cc_id)},
        )
        return int(row.scalar_one() or 0)
    except Exception:
        return 0


async def delete(db: AsyncSession, cc: CostCenter) -> None:
    await db.delete(cc)
    await db.flush()
