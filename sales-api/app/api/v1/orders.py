"""Orders (P3): list, detail, the review re-check and manual orders.
Permissions are checked by the services; each write commits once."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.core.deps import get_uow
from app.platform.crud_router import page_body
from app.platform.query import QuerySpec, query_spec
from app.runtime import Runtime, get_runtime
from app.schemas.orders import LinePreview, ManualLineIn, ManualOrderIn, OrderDetail, OrderRead, ReviewResult
from app.services.order_views import ManualOrderService, OrderService

UoW = Annotated[Any, Depends(get_uow)]
Rt = Annotated[Runtime, Depends(get_runtime)]
Spec = Annotated[QuerySpec, Depends(query_spec)]
Version = Annotated[int | None, Query()]

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("")
async def list_orders(u: UoW, spec: Spec) -> dict:
    return page_body(await OrderService(u).list(spec))


@router.get("/eligibility-preview")
async def eligibility_preview(u: UoW, rt: Rt) -> list[dict]:
    return await OrderService(u).eligibility_preview(rt.outbound)


@router.post("/{id}/send-940")
async def send_940_now(u: UoW, rt: Rt, id: int, replace: bool = False) -> dict:
    out = await OrderService(u).send_940_now(id, rt.outbound, replace=replace)
    await u.commit()
    return out


@router.get("/{id}/shipments/{shipment_id}/packing-list.pdf")
async def packing_list(u: UoW, id: int, shipment_id: int) -> Response:
    name, pdf = await OrderService(u).packing_list(id, shipment_id)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.post("/shipments/{shipment_id}/shopify-retry")
async def retry_shopify(u: UoW, rt: Rt, shipment_id: int) -> dict:
    out = await OrderService(u).retry_shopify(shipment_id, rt.shipments)
    await u.commit()
    return out


@router.get("/meta")
async def orders_meta(u: UoW) -> dict:
    return await OrderService(u).meta()


@router.post("/manual/preview", response_model=list[LinePreview])
async def manual_preview(u: UoW, lines: list[ManualLineIn]):
    return await ManualOrderService(u).preview(lines)  # reads only


@router.post("/manual", response_model=OrderRead, status_code=201)
async def manual_create(u: UoW, data: ManualOrderIn):
    o = await ManualOrderService(u).create(data)
    await u.commit()
    return OrderService(u).to_read(o)


@router.put("/manual/{id}", response_model=OrderRead)
async def manual_update(u: UoW, id: int, data: ManualOrderIn, version: Version = None):
    o = await ManualOrderService(u).update(id, data, version)
    await u.commit()
    return OrderService(u).to_read(o)


@router.post("/manual/{id}/submit", response_model=OrderRead)
async def manual_submit(u: UoW, id: int, version: Version = None):
    o = await ManualOrderService(u).submit(id, version)
    await u.commit()
    return OrderService(u).to_read(o)


@router.post("/manual/{id}/cancel", response_model=OrderRead)
async def manual_cancel(u: UoW, id: int, version: Version = None):
    o = await ManualOrderService(u).cancel(id, version)
    await u.commit()
    return OrderService(u).to_read(o)


@router.get("/{id}", response_model=OrderDetail)
async def order_detail(u: UoW, id: int):
    return await OrderService(u).detail(id)


@router.post("/{id}/resolve-review", response_model=ReviewResult)
async def resolve_review(u: UoW, rt: Rt, id: int):
    out = await OrderService(u).resolve_review(id, rt.intake.tasks, rt.container.shopify)
    await u.commit()
    return out
