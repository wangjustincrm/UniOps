import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.crud import company as company_crud
from app.schemas.company import CompanyCreate, CompanyResponse, CompanyUpdate

router = APIRouter(prefix="/companies", tags=["companies"])

_ADMIN_ROLES = {"system_admin"}


def _require_admin(user: dict):
    if user.get("role") not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="system_admin role required")


@router.get("", response_model=list[CompanyResponse])
async def list_companies(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    active_only: bool = Query(default=False),
):
    return await company_crud.get_all(db, active_only=active_only)


@router.get("/by-code/{code}", response_model=CompanyResponse)
async def get_company_by_code(code: str, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    c = await company_crud.get_by_code(db, code)
    if not c:
        raise HTTPException(status_code=404, detail="Company not found")
    return c


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(company_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    c = await company_crud.get_by_id(db, company_id)
    if not c:
        raise HTTPException(status_code=404, detail="Company not found")
    return c


@router.post("", response_model=CompanyResponse, status_code=201)
async def create_company(
    body: CompanyCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = ...,
):
    _require_admin(user)
    if await company_crud.get_by_code(db, body.code):
        raise HTTPException(status_code=409, detail="Company code already exists")
    return await company_crud.create(db, body)


@router.patch("/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: uuid.UUID,
    body: CompanyUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = ...,
):
    _require_admin(user)
    c = await company_crud.get_by_id(db, company_id)
    if not c:
        raise HTTPException(status_code=404, detail="Company not found")
    return await company_crud.update(db, c, body)
