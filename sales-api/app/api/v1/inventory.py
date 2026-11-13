"""The Radiant inventory (design §15): by item, by lot, the moves (a stock card), CSV, and a
person's adjustment. The services check the permissions."""
from typing import Annotated, Any

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from app.core.deps import get_uow
from app.platform.crud_router import page_body
from app.platform.query import QuerySpec, query_spec
from app.schemas.stock import (AdjustIn, CountCompareRow, CountUndoIn, CountUpdateIn, CountVersionIn, StockCountLineRead,
                               StockCountRead, StockMoveRead)
from app.services.record_files import MAX_BYTES
from app.services.stock import StockItemService, StockLotService, StockMoveService
from app.services.stock_counts import StockCountService

UoW = Annotated[Any, Depends(get_uow)]
Spec = Annotated[QuerySpec, Depends(query_spec)]

router = APIRouter(prefix="/inventory", tags=["inventory"])

for name, svc, file in (("items", StockItemService, "inventory-items.csv"), ("lots", StockLotService, "inventory-lots.csv"),
                        ("moves", StockMoveService, "inventory-moves.csv")):
    def _routes(name=name, svc=svc, file=file):
        @router.get(f"/{name}", name=f"inventory_{name}")
        async def lst(u: UoW, spec: Spec) -> dict:
            return page_body(await svc(u).list(spec))

        @router.get(f"/{name}/meta", name=f"inventory_{name}_meta")
        async def meta(u: UoW) -> dict:
            return await svc(u).meta()

        @router.get(f"/{name}/suggest", name=f"inventory_{name}_suggest")
        async def suggest(u: UoW, field: str, q: str = "") -> list[str]:
            return await svc(u).suggest(field, q[:100])

        @router.get(f"/{name}/export.csv", name=f"inventory_{name}_csv")
        async def export(u: UoW, spec: Spec) -> Response:
            return Response(await svc(u).export_csv(spec), media_type="text/csv",
                            headers={"Content-Disposition": f'attachment; filename="{file}"'})
    _routes()


@router.post("/adjust", response_model=StockMoveRead)
async def adjust(u: UoW, body: AdjustIn):
    m = await StockMoveService(u).adjust(item_code=body.item_code, lot=body.lot, qty_ea=body.qty_ea, reason=body.reason)
    await u.commit()
    return StockMoveRead.model_validate(m, from_attributes=True)


# ── Radiant's inventory reports (counts, user 2026-11-13) ────────────────────

@router.get("/counts", name="inventory_counts")
async def counts(u: UoW, spec: Spec) -> dict:
    return page_body(await StockCountService(u).list(spec))


@router.get("/counts/meta", name="inventory_counts_meta")
async def counts_meta(u: UoW) -> dict:
    return await StockCountService(u).meta()


@router.post("/counts", response_model=StockCountRead, status_code=201)
async def upload_count(u: UoW, file: Annotated[UploadFile, File()], counted_at: Annotated[datetime, Form()],
                       note: Annotated[str | None, Form()] = None):
    data = await file.read(MAX_BYTES + 1)
    out = await StockCountService(u).create(name=file.filename or "inventory", data=data, counted_at=counted_at, note=note)
    await u.commit()
    return out


@router.get("/counts/{count_id}", response_model=StockCountRead)
async def get_count(u: UoW, count_id: int):
    return await StockCountService(u).get(count_id)


@router.put("/counts/{count_id}", response_model=StockCountRead)
async def update_count(u: UoW, count_id: int, body: CountUpdateIn):
    out = await StockCountService(u).update(count_id, version=body.version, counted_at=body.counted_at, note=body.note)
    await u.commit()
    return out


@router.get("/counts/{count_id}/lines", response_model=list[StockCountLineRead])
async def count_lines(u: UoW, count_id: int):
    return await StockCountService(u).lines(count_id)


@router.get("/counts/{count_id}/compare", response_model=list[CountCompareRow])
async def compare_count(u: UoW, count_id: int):
    return await StockCountService(u).compare(count_id)


@router.post("/counts/{count_id}/apply", response_model=StockCountRead)
async def apply_count(u: UoW, count_id: int, body: CountVersionIn):
    out = await StockCountService(u).apply(count_id, version=body.version)
    await u.commit()
    return out


@router.post("/counts/{count_id}/undo", response_model=StockCountRead)
async def undo_count(u: UoW, count_id: int, body: CountUndoIn):
    out = await StockCountService(u).undo(count_id, version=body.version, reason=body.reason)
    await u.commit()
    return out
