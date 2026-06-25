"""Tax master + determination API (Phase 0-B2).

GET    /tax/codes      — tax codes (active-on-date for dropdowns; ?all=true for admin)
POST   /tax/codes      — create a tax code / rate version (Finance Tax Settings)
PATCH  /tax/codes/{id} — edit a tax code
DELETE /tax/codes/{id} — soft-deactivate a tax code
GET    /tax/determine  — place-of-supply determination (FIN-TAX-002)

Reads: any authenticated role. Writes: system_admin | finance_manager | ap_clerk.
Rates/rules are maintained as data; no treatment is hardcoded.
"""
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_roles
from app.crud import tax as tax_crud
from app.crud.tax import TaxDeterminationError
from app.db.base import get_db
from app.schemas.tax import TaxCodeCreate, TaxCodeOut, TaxCodeUpdate, TaxDetermination

router = APIRouter(prefix="/tax", tags=["tax"])

WriteDep = Annotated[dict, Depends(require_roles("system_admin", "finance_manager", "ap_clerk"))]


@router.get("/codes", response_model=list[TaxCodeOut])
async def list_tax_codes(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    as_of: date | None = Query(default=None),
    all: bool = Query(default=False, description="Admin view: every code/version, not just active-on-date"),
):
    if all:
        return await tax_crud.list_all_codes(db)
    return await tax_crud.list_codes(db, as_of=as_of)


@router.post("/codes", response_model=TaxCodeOut, status_code=status.HTTP_201_CREATED)
async def create_tax_code(
    body: TaxCodeCreate,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    if await tax_crud.get_code_version(db, body.code, body.effective_from):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tax code '{body.code}' already has a version effective {body.effective_from}",
        )
    return await tax_crud.create_code(db, body)


@router.patch("/codes/{code_id}", response_model=TaxCodeOut)
async def update_tax_code(
    code_id: uuid.UUID,
    body: TaxCodeUpdate,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    row = await tax_crud.get_code_by_id(db, code_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tax code not found")
    return await tax_crud.update_code(db, row, body)


@router.delete("/codes/{code_id}", response_model=TaxCodeOut)
async def deactivate_tax_code(
    code_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    # Soft delete: documents that snapshotted this code keep their tax_code/rate,
    # so we never hard-delete. Returns the updated row.
    row = await tax_crud.get_code_by_id(db, code_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tax code not found")
    return await tax_crud.deactivate_code(db, row)


@router.get("/determine", response_model=TaxDetermination)
async def determine_tax(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    direction: str = Query(default="any", pattern="^(purchase|sale|any)$"),
    province: str | None = Query(default=None, min_length=2, max_length=2),
    customer_type: str | None = Query(default=None),
    item_tax_class: str | None = Query(default=None),
    as_of: date | None = Query(default=None),
):
    try:
        result = await tax_crud.determine(
            db, direction=direction, province=province.upper() if province else None,
            customer_type=customer_type, item_tax_class=item_tax_class, as_of=as_of,
        )
    except TaxDeterminationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result
