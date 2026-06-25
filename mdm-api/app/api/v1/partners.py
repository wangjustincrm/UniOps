"""Business Partner CRUD — the master entry for suppliers AND customers (B3).

EPMS's /vendors API remains as the procurement-facing supplier view of the
same table; CRM (Phase b) attaches customers here.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_roles
from app.db.base import get_db
from app.models.business_partner import BusinessPartner
from app.schemas.business_partner import (
    PartnerCreate, PartnerListResponse, PartnerOut, PartnerUpdate,
)

router = APIRouter(prefix="/partners", tags=["business-partners"])

PartnerWriteDep = Annotated[dict, Depends(
    require_roles("system_admin", "vendor_manager", "finance_manager")
)]


@router.get("", response_model=PartnerListResponse)
async def list_partners(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    role: str | None = Query(default=None, pattern="^(supplier|customer)$"),
    search: str | None = Query(default=None),
    active_only: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    q = select(BusinessPartner)
    if role == "supplier":
        q = q.where(BusinessPartner.is_supplier.is_(True))
    elif role == "customer":
        q = q.where(BusinessPartner.is_customer.is_(True))
    if active_only:
        q = q.where(BusinessPartner.is_active.is_(True))
    if search:
        like = f"%{search}%"
        q = q.where(or_(BusinessPartner.name.ilike(like), BusinessPartner.code.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(
        q.order_by(BusinessPartner.name).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return PartnerListResponse(items=[PartnerOut.model_validate(r) for r in rows], total=total)


@router.get("/{partner_id}", response_model=PartnerOut)
async def get_partner(
    partner_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    row = (await db.execute(
        select(BusinessPartner).where(BusinessPartner.id == partner_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Partner not found")
    return row


@router.post("", response_model=PartnerOut, status_code=201)
async def create_partner(
    body: PartnerCreate,
    _: PartnerWriteDep,
    db: AsyncSession = Depends(get_db),
):
    partner = BusinessPartner(**body.model_dump())
    db.add(partner)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"Partner code '{body.code}' already exists")
    await db.commit()
    return partner


@router.patch("/{partner_id}", response_model=PartnerOut)
async def update_partner(
    partner_id: uuid.UUID,
    body: PartnerUpdate,
    _: PartnerWriteDep,
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(
        select(BusinessPartner).where(BusinessPartner.id == partner_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Partner not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await db.flush()
    await db.commit()
    return row
