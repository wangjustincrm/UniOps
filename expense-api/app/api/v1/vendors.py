"""Vendor lookup endpoint — read-only mirror of EPMS vendors table."""
import uuid
from sqlalchemy import select, or_
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.core.deps import CurrentUserDep, SessionDep
from app.models.epms_mirrors import EpmsVendor

router = APIRouter(prefix="/vendors", tags=["vendors"])


class VendorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    code: str


class VendorListResponse(BaseModel):
    items: list[VendorOut]
    total: int


@router.get("", response_model=VendorListResponse)
async def list_vendors(
    db: SessionDep,
    _: CurrentUserDep,
    search: str | None = None,
    active_only: bool = True,
    page_size: int = 50,
):
    q = select(EpmsVendor).where(EpmsVendor.is_active.is_(True)) if active_only else select(EpmsVendor)
    if search:
        term = f"%{search}%"
        q = q.where(or_(EpmsVendor.name.ilike(term), EpmsVendor.code.ilike(term)))
    q = q.order_by(EpmsVendor.name).limit(page_size)
    rows = (await db.execute(q)).scalars().all()
    return VendorListResponse(items=rows, total=len(rows))
