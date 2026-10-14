"""Settings page: parameters, NC warehouse classes, no-batch materials.

Reads are gated inventory.view (the comparison page needs to show which
warehouses count as WMS); writes are gated inventory.admin.

Warehouse classes are listed as the UNION of what NC has (inv_nc_warehouse)
and what has been classified, so a warehouse NC added since the last edit
shows up as `unclassified` instead of being invisible — an unclassified
warehouse never takes part in the WMS comparison (design §4.6).
"""
import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text

from app.api.v1.perms import AdminDep, ViewDep
from app.core.deps import SessionDep
from app.models import InvNoBatchMaterial, InvWarehouseClass
from app.models.settings import WAREHOUSE_CLASSES
from app.services import params

router = APIRouter(prefix="/settings", tags=["settings"])


def _actor(payload: dict) -> uuid.UUID | None:
    try:
        return uuid.UUID(payload.get("sub", ""))
    except ValueError:
        return None


# ── Parameters ───────────────────────────────────────────────────────────────

@router.get("/params")
async def get_params(db: SessionDep, _: ViewDep) -> dict[str, Any]:
    return {
        "values": await params.get_all(db),
        "defaults": params.DEFAULTS,
        "nc_time_zones": list(params.NC_TIME_ZONES),
    }


class ParamWrite(BaseModel):
    value: Any


@router.put("/params/{key}")
async def put_param(key: str, body: ParamWrite, db: SessionDep, payload: AdminDep) -> dict:
    try:
        value = await params.set_param(db, key, body.value, _actor(payload))
    except params.InvalidParam as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"key": key, "value": value}


@router.get("/wms-history")
async def wms_history(db: SessionDep, _: ViewDep) -> dict:
    """When WMS figures begin — for any page that shows WMS data."""
    from app.services import recon
    return recon.wms_history_info(await recon.wms_history_start(db))


# ── Warehouse classes ───────────────────────────────────────────────────────

@router.get("/warehouse-classes")
async def list_warehouse_classes(db: SessionDep, _: ViewDep) -> dict:
    rows = (await db.execute(text("""
        with nc as (
            select code, max(name) as name, bool_and(iscalculatedinvcost) as costed
            from inv_nc_warehouse group by code),
        stock as (
            select w.code, sum(o.nonhandnum) as qty
            from inv_nc_ic_onhand o join inv_nc_warehouse w on w.pk_stordoc = o.cwarehouseid
            group by w.code)
        select coalesce(nc.code, c.nc_code) as code, nc.name, nc.costed,
               c.warehouse_class, c.note, c.updated_at, coalesce(stock.qty, 0) as qty,
               nc.code is not null as in_nc
        from nc full join inv_warehouse_class c on c.nc_code = nc.code
        left join stock on stock.code = coalesce(nc.code, c.nc_code)
        order by coalesce(nc.code, c.nc_code)"""))).all()
    return {
        "classes": list(WAREHOUSE_CLASSES),
        "items": [{
            "code": r.code, "name": r.name,
            "costed": r.costed,                 # False = 不计成本 (SC16)
            "warehouse_class": r.warehouse_class or "unclassified",
            "note": r.note,
            "on_hand_qty": str(r.qty),
            "in_nc": r.in_nc,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        } for r in rows],
        "unclassified_count": sum(1 for r in rows if r.warehouse_class is None and r.in_nc),
    }


class WarehouseClassWrite(BaseModel):
    warehouse_class: str
    note: str | None = Field(None, max_length=500)


@router.put("/warehouse-classes/{code}")
async def put_warehouse_class(code: str, body: WarehouseClassWrite, db: SessionDep,
                              payload: AdminDep) -> dict:
    if body.warehouse_class not in WAREHOUSE_CLASSES:
        raise HTTPException(status_code=422, detail=f"Class must be one of {', '.join(WAREHOUSE_CLASSES)}")
    code = code.strip()
    if not code or len(code) > 40:
        raise HTTPException(status_code=422, detail="Warehouse code is required (max 40 characters)")
    row = await db.get(InvWarehouseClass, code)
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(InvWarehouseClass(nc_code=code, warehouse_class=body.warehouse_class,
                                 note=body.note, updated_by=_actor(payload), updated_at=now))
    else:
        row.warehouse_class, row.note = body.warehouse_class, body.note
        row.updated_by, row.updated_at = _actor(payload), now
    return {"code": code, "warehouse_class": body.warehouse_class}


# ── Materials not tracked by batch in NC ────────────────────────────────────

@router.get("/no-batch-materials")
async def list_no_batch(db: SessionDep, _: ViewDep) -> list[dict]:
    rows = (await db.execute(select(InvNoBatchMaterial).order_by(InvNoBatchMaterial.material_code))).scalars().all()
    return [{"material_code": r.material_code, "since_date": r.since_date.isoformat(),
             "note": r.note} for r in rows]


class NoBatchWrite(BaseModel):
    since_date: date
    note: str | None = Field(None, max_length=500)


@router.put("/no-batch-materials/{material_code}")
async def put_no_batch(material_code: str, body: NoBatchWrite, db: SessionDep,
                       payload: AdminDep) -> dict:
    code = material_code.strip().upper()
    if not code or len(code) > 60:
        raise HTTPException(status_code=422, detail="Material code is required (max 60 characters)")
    row = await db.get(InvNoBatchMaterial, code)
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(InvNoBatchMaterial(material_code=code, since_date=body.since_date, note=body.note,
                                  updated_by=_actor(payload), updated_at=now))
    else:
        row.since_date, row.note = body.since_date, body.note
        row.updated_by, row.updated_at = _actor(payload), now
    return {"material_code": code, "since_date": body.since_date.isoformat()}


@router.delete("/no-batch-materials/{material_code}", status_code=204)
async def delete_no_batch(material_code: str, db: SessionDep, _: AdminDep) -> None:
    await db.execute(delete(InvNoBatchMaterial).where(
        InvNoBatchMaterial.material_code == material_code.strip().upper()))
