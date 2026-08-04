"""material_suppliers CRUD — supply parameters, hand-maintained (MRP phase0
task 6). Phase 1 purchase-suggestion logic picks the default supplier + lead
time for a material via `is_primary`.

Reads: any authenticated role (materials.py/boms.py idiom). Writes: gated on
require_any_permission("data_maintenance", "mdm.bom.write") — same rationale
as boms.py's POST /sync (see that module's docstring): this table is
material/supplier governance data, so it accepts the narrower `mdm.bom.write`
key too, without dropping the pre-existing `data_maintenance` admins.
"""
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_any_permission
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.material_supplier import MaterialSupplier

router = APIRouter(prefix="/material-suppliers", tags=["material-suppliers"])

WriteDep = Annotated[dict, Depends(require_any_permission("data_maintenance", "mdm.bom.write"))]


class MaterialSupplierCreate(BaseModel):
    material_code: str
    partner_code: str
    lead_time_days: int | None = None
    moq: Decimal | None = None
    order_multiple: Decimal | None = None
    is_primary: bool = False
    price_ref: Decimal | None = None
    notes: str | None = None


class MaterialSupplierUpdate(BaseModel):
    lead_time_days: int | None = None
    moq: Decimal | None = None
    order_multiple: Decimal | None = None
    is_primary: bool | None = None
    price_ref: Decimal | None = None
    notes: str | None = None


class MaterialSupplierResponse(BaseModel):
    id: uuid.UUID
    material_code: str
    partner_code: str
    lead_time_days: int | None
    moq: Decimal | None
    order_multiple: Decimal | None
    is_primary: bool
    price_ref: Decimal | None
    notes: str | None

    model_config = {"from_attributes": True}


class MaterialSupplierListResponse(BaseModel):
    items: list[MaterialSupplierResponse]
    total: int
    page: int
    page_size: int


def _conflict_detail(material_code: str, partner_code: str) -> str:
    return (
        f"material_suppliers row for material_code={material_code!r} "
        f"partner_code={partner_code!r} already exists"
    )


@router.get("", response_model=MaterialSupplierListResponse)
async def list_material_suppliers(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    material_code: str | None = Query(default=None),
    partner_code: str | None = Query(default=None),
    is_primary: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    query = select(MaterialSupplier)
    if material_code:
        query = query.where(MaterialSupplier.material_code == material_code)
    if partner_code:
        query = query.where(MaterialSupplier.partner_code == partner_code)
    if is_primary is not None:
        query = query.where(MaterialSupplier.is_primary.is_(is_primary))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = list((await db.execute(
        query.order_by(MaterialSupplier.material_code, MaterialSupplier.partner_code)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return MaterialSupplierListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=MaterialSupplierResponse, status_code=status.HTTP_201_CREATED)
async def create_material_supplier(
    body: MaterialSupplierCreate,
    _: WriteDep,
    db: AsyncSession = Depends(get_db),
):
    row = MaterialSupplier(**body.model_dump())
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_conflict_detail(body.material_code, body.partner_code),
        )
    await db.commit()
    return row


@router.patch("/{row_id}", response_model=MaterialSupplierResponse)
async def update_material_supplier(
    row_id: uuid.UUID,
    body: MaterialSupplierUpdate,
    _: WriteDep,
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(
        select(MaterialSupplier).where(MaterialSupplier.id == row_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="material_suppliers row not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    # Capture BEFORE flush: after a flush raises, the session's transaction
    # is left in a state that requires rollback before any further ORM
    # attribute access — reading `row.material_code`/`row.partner_code` in
    # the `except` branch below would trigger an implicit re-SELECT (an
    # expired attribute load) against that dead transaction and raise
    # PendingRollbackError instead of cleanly returning 409. This path was
    # previously unreachable (the old, unfiltered material_code+partner_code
    # unique constraint could never be hit by a PATCH, which doesn't touch
    # either field) — the new partial `is_primary` index (migration 0013)
    # makes it reachable, so it needs to actually work now.
    material_code, partner_code = row.material_code, row.partner_code
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_conflict_detail(material_code, partner_code),
        )
    await db.commit()
    return row


@router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material_supplier(
    row_id: uuid.UUID,
    _: WriteDep,
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(
        select(MaterialSupplier).where(MaterialSupplier.id == row_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="material_suppliers row not found")
    await db.delete(row)
    await db.commit()
