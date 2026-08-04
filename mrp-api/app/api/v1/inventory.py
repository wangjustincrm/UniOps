"""Browse the WMS inventory lot mirror (Task 8).

Read-only: `GET /inventory/lots` supports the filters Phase 1's engine will
also need (material_code, mapped_status) plus pagination. Gated
`mrp.report.view` (seeded in Task 9; system_admin bypasses, see
app/core/authz.py).

Availability quotation for Phase 1 (design doc appendix A / task brief):
`available = qty - qty_onhold` for lots where `mapped_status='available'`
(status=02/Release AND not expired). This endpoint does not compute that
aggregate itself — it is a raw browse of the mirror; a future Phase 1
availability endpoint will apply that formula on top of these rows.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.models.wms_inventory import WmsInventoryLot

router = APIRouter(prefix="/inventory", tags=["inventory"])

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]


class WmsInventoryLotResponse(BaseModel):
    id: uuid.UUID
    warehouse_id: str
    material_code: str
    lot_no: str
    qty: Decimal
    qty_allocated: Decimal
    qty_onhold: Decimal
    wms_status: str | None
    mapped_status: str
    production_date: date | None
    expiry_date: date | None
    inbound_date: date | None
    supplier_batch: str | None
    supplier_code: str | None
    source_doc: str | None
    wms_edit_time: datetime | None
    sync_batch_id: str

    model_config = {"from_attributes": True}


class WmsInventoryLotListResponse(BaseModel):
    items: list[WmsInventoryLotResponse]
    total: int


@router.get("/lots", response_model=WmsInventoryLotListResponse)
async def list_lots(
    db: SessionDep,
    _: ReadDep,
    material_code: str | None = Query(default=None),
    mapped_status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    stmt = select(WmsInventoryLot)
    count_stmt = select(func.count()).select_from(WmsInventoryLot)
    if material_code:
        stmt = stmt.where(WmsInventoryLot.material_code == material_code)
        count_stmt = count_stmt.where(WmsInventoryLot.material_code == material_code)
    if mapped_status:
        stmt = stmt.where(WmsInventoryLot.mapped_status == mapped_status)
        count_stmt = count_stmt.where(WmsInventoryLot.mapped_status == mapped_status)

    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(WmsInventoryLot.material_code, WmsInventoryLot.lot_no)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()
    return {"items": items, "total": total}
