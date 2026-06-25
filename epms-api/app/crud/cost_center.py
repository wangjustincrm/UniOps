"""CRUD operations for CostCenter."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_center import CostCenter
from app.models.pr import PurchaseRequest
from app.schemas.department import CostCenterCreate, CostCenterUpdate


async def get_all(
    db: AsyncSession,
    department_id: uuid.UUID | None = None,
    active_only: bool = False,
) -> list[CostCenter]:
    q = select(CostCenter)
    if department_id is not None:
        q = q.where(CostCenter.department_id == department_id)
    if active_only:
        q = q.where(CostCenter.is_active.is_(True))
    result = await db.execute(q.order_by(CostCenter.code))
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, cc_id: uuid.UUID) -> CostCenter | None:
    result = await db.execute(select(CostCenter).where(CostCenter.id == cc_id))
    return result.scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> CostCenter | None:
    result = await db.execute(select(CostCenter).where(CostCenter.code == code.upper()))
    return result.scalar_one_or_none()


async def create(db: AsyncSession, payload: CostCenterCreate) -> CostCenter:
    cc = CostCenter(
        code=payload.code.upper(),
        name=payload.name,
        department_id=payload.department_id,
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
    # Budget plan references are tracked in budget-api; querying them is
    # optional and not blocking for CC deletion. We only block on PR
    # references here (still owned by epms-api).
    pr_count = (await db.execute(
        select(func.count()).where(PurchaseRequest.cost_center_id == cc_id)
    )).scalar_one()
    return pr_count


async def delete(db: AsyncSession, cc: CostCenter) -> None:
    await db.delete(cc)
    await db.flush()
