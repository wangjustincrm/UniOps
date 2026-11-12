"""The Radiant inventory (design §15): by item, by lot, the moves (a stock card), CSV, and a
person's adjustment. The services check the permissions."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from app.core.deps import get_uow
from app.platform.crud_router import page_body
from app.platform.query import QuerySpec, query_spec
from app.schemas.stock import AdjustIn, StockMoveRead
from app.services.stock import StockItemService, StockLotService, StockMoveService

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
