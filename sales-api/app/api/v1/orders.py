"""Orders (P3): list, detail, manual orders and the forms of an order; its record actions (POST /orders/actions/…) are declared
in app/services/order_actions.py.
Permissions are checked by the services; each write commits by itself (platform/routes.py)."""
from typing import Annotated, Any

from fastapi import Depends, Query, Response

from app.core.deps import get_uow
from app.platform.crud_router import resource_router
from app.platform.files import FileResult
from app.platform.routes import Router, own_commit
from app.runtime import Runtime, get_runtime
from app.schemas.orders import (
    AmendOrderIn, InvoicePricesIn, ReturnedIn, LinePreview, ManualLineIn, ManualOrderIn, OrderDetail, OrderRead, ResolveRowIn,
)
from app.services.order_holds import OrderActions
from app.services.order_views import ManualOrderService, OrderService

UoW = Annotated[Any, Depends(get_uow)]
Rt = Annotated[Runtime, Depends(get_runtime)]
Version = Annotated[int | None, Query()]

routes = Router(prefix="/orders", tags=["orders"])


@routes.get("/eligibility-preview")
async def eligibility_preview(u: UoW, rt: Rt) -> list[dict]:
    return await OrderService(u).eligibility_preview(rt.outbound)


@routes.post("/{id}/amend")
async def amend_order(u: UoW, rt: Rt, id: int, body: AmendOrderIn, version: Version = None) -> dict:
    return await OrderService(u).amend(id, body, rt.outbound, version)


@routes.get("/{id}/shipments/{shipment_id}/packing-list.pdf")
async def packing_list(u: UoW, id: int, shipment_id: int) -> Response:
    name, pdf = await OrderService(u).packing_list(id, shipment_id)
    return FileResult(pdf, name, media_type="application/pdf", inline=True)


@routes.post("/shipments/{shipment_id}/shopify-retry")
async def retry_shopify(u: UoW, rt: Rt, shipment_id: int) -> dict:
    return await OrderService(u).retry_shopify(shipment_id, rt.shipments)


@routes.post("/shipments/{shipment_id}/rows/{row_id}/resolve", response_model=OrderRead)
@own_commit("reads the order again after the commit")
async def resolve_945_row(u: UoW, rt: Rt, shipment_id: int, row_id: int, body: ResolveRowIn):
    o = await OrderActions(u, rt.tasks).resolve_row(shipment_id, row_id, body.order_line_id, body.reason)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@routes.post("/manual/preview", response_model=list[LinePreview])
@own_commit("reads only: the EA preview before saving (FR-OB-19a)")
async def manual_preview(u: UoW, lines: list[ManualLineIn]):
    return await ManualOrderService(u).preview(lines)  # reads only


@routes.post("/manual", response_model=OrderRead, status_code=201)
@own_commit("reads the order again after the commit")
async def manual_create(u: UoW, data: ManualOrderIn):
    o = await ManualOrderService(u).create(data)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@routes.put("/manual/{id}", response_model=OrderRead)
@own_commit("reads the order again after the commit")
async def manual_update(u: UoW, id: int, data: ManualOrderIn, version: Version = None):
    o = await ManualOrderService(u).update(id, data, version)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@routes.post("/{id}/reorder", response_model=OrderRead)
@own_commit("reads the order again after the commit")
async def reorder(u: UoW, id: int):
    """Reorder for this customer (user 2026-11-19): a Draft manual order copied from this one, no lines."""
    o = await ManualOrderService(u).reorder(id)
    await u.commit()
    return await OrderService(u).get(o.id)


@routes.put("/{id}/returned", response_model=OrderDetail)
async def set_returned(u: UoW, id: int, body: ReturnedIn, version: Version = None):
    return await OrderService(u).set_returned(id, body, version)


@routes.put("/{id}/invoice-prices", response_model=OrderDetail)
async def set_invoice_prices(u: UoW, id: int, body: InvoicePricesIn, version: Version = None):
    return await OrderService(u).set_invoice_prices(id, body, version)


router = resource_router(OrderService, uow=get_uow, custom=routes, runtime=get_runtime)
