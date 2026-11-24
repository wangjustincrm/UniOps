"""The Radiant inventory (design §15): by item, by lot, the moves (a stock card), CSV, and a
person's adjustment. The services check the permissions."""
from typing import Annotated, Any

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from app.core.deps import get_uow
from app.platform.crud_router import page_body, resource_router
from app.platform.query import QuerySpec, export_columns, query_spec
from app.schemas.stock import (AdjustIn, CountCompareRow, CountUndoIn, CountUpdateIn, CountVersionIn, StockCountLineRead,
                               StockCountRead, StockMoveRead)
from app.services.record_files import MAX_BYTES
from app.services.stock import StockItemService, StockLotService, StockMoveService
from app.services.stock_counts import StockCountService

UoW = Annotated[Any, Depends(get_uow)]
Spec = Annotated[QuerySpec, Depends(query_spec)]

routes = APIRouter(prefix="/inventory", tags=["inventory"])

for name, svc, file in (("lots", StockLotService, "inventory-lots.csv"),
                        ("moves", StockMoveService, "inventory-moves.csv")):
    def _routes(name=name, svc=svc, file=file):
        @routes.get(f"/{name}", name=f"inventory_{name}")
        async def lst(u: UoW, spec: Spec) -> dict:
            return page_body(await svc(u).list(spec))

        @routes.get(f"/{name}/meta", name=f"inventory_{name}_meta")
        async def meta(u: UoW) -> dict:
            return await svc(u).meta()

        @routes.get(f"/{name}/suggest", name=f"inventory_{name}_suggest")
        async def suggest(u: UoW, field: str, q: str = "") -> list[str]:
            return await svc(u).suggest(field, q[:100])

        @routes.get(f"/{name}/export.csv", name=f"inventory_{name}_csv")
        async def export(u: UoW, spec: Spec, columns: Annotated[list[str] | None, Depends(export_columns)] = None) -> Response:
            return Response(await svc(u).export_csv(spec, columns), media_type="text/csv",
                            headers={"Content-Disposition": f'attachment; filename="{file}"'})
    _routes()


@routes.post("/adjust", response_model=StockMoveRead)
async def adjust(u: UoW, body: AdjustIn):
    m = await StockMoveService(u).adjust(item_code=body.item_code, lot=body.lot, qty_ea=body.qty_ea, reason=body.reason)
    await u.commit()
    return StockMoveRead.model_validate(m, from_attributes=True)


# ── Radiant's inventory reports (counts, user 2026-11-13) ────────────────────

@routes.get("/counts", name="inventory_counts")
async def counts(u: UoW, spec: Spec) -> dict:
    return page_body(await StockCountService(u).list(spec))


@routes.get("/counts/meta", name="inventory_counts_meta")
async def counts_meta(u: UoW) -> dict:
    return await StockCountService(u).meta()


@routes.post("/counts", response_model=StockCountRead, status_code=201)
async def upload_count(u: UoW, file: Annotated[UploadFile, File()], counted_at: Annotated[datetime, Form()],
                       note: Annotated[str | None, Form()] = None):
    data = await file.read(MAX_BYTES + 1)
    out = await StockCountService(u).create(name=file.filename or "inventory", data=data, counted_at=counted_at, note=note)
    await u.commit()
    return out


@routes.get("/counts/{count_id}", response_model=StockCountRead)
async def get_count(u: UoW, count_id: int):
    return await StockCountService(u).get(count_id)


@routes.put("/counts/{count_id}", response_model=StockCountRead)
async def update_count(u: UoW, count_id: int, body: CountUpdateIn):
    out = await StockCountService(u).update(count_id, version=body.version, counted_at=body.counted_at, note=body.note)
    await u.commit()
    return out


@routes.get("/counts/{count_id}/lines", response_model=list[StockCountLineRead])
async def count_lines(u: UoW, count_id: int):
    return await StockCountService(u).lines(count_id)


@routes.get("/counts/{count_id}/compare", response_model=list[CountCompareRow])
async def compare_count(u: UoW, count_id: int):
    return await StockCountService(u).compare(count_id)


@routes.post("/counts/{count_id}/apply", response_model=StockCountRead)
async def apply_count(u: UoW, count_id: int, body: CountVersionIn):
    out = await StockCountService(u).apply(count_id, version=body.version)
    await u.commit()
    return out


@routes.post("/counts/{count_id}/undo", response_model=StockCountRead)
async def undo_count(u: UoW, count_id: int, body: CountUndoIn):
    out = await StockCountService(u).undo(count_id, version=body.version, reason=body.reason)
    await u.commit()
    return out


router = APIRouter()
for r in (resource_router(StockItemService, uow=get_uow),
          routes):  # the lists' fixed paths before the hand-written ones
    router.include_router(r)
