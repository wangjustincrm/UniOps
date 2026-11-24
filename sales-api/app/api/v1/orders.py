"""Orders (P3): list, detail, the review re-check and manual orders.
Permissions are checked by the services; each write commits once."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.core.deps import get_uow
from app.platform.crud_router import resource_router
from app.platform.files import FileResult
from app.runtime import Runtime, get_runtime
from app.schemas.orders import (
    AmendOrderIn, CancelOrderIn, InvoicePricesIn, ReturnedIn, LinePreview, ManualLineIn, ManualOrderIn, OrderDetail, OrderRead,
    ReleaseHoldIn, ResolveRowIn, ReviewResult, Send940In, SettleIn, UnsettleIn,
)
from app.services.order_holds import OrderActions
from app.services.order_payment import OrderPaymentService
from app.services.order_views import ManualOrderService, OrderService

UoW = Annotated[Any, Depends(get_uow)]
Rt = Annotated[Runtime, Depends(get_runtime)]
Version = Annotated[int | None, Query()]

routes = APIRouter(prefix="/orders", tags=["orders"])


@routes.get("/eligibility-preview")
async def eligibility_preview(u: UoW, rt: Rt) -> list[dict]:
    return await OrderService(u).eligibility_preview(rt.outbound)


@routes.post("/{id}/amend")
async def amend_order(u: UoW, rt: Rt, id: int, body: AmendOrderIn, version: Version = None) -> dict:
    out = await OrderService(u).amend(id, body, rt.outbound, version)
    await u.commit()
    return out


@routes.post("/{id}/send-940")
async def send_940_now(u: UoW, rt: Rt, id: int, replace: bool = False, version: Version = None, body: Send940In | None = None) -> dict:
    out = await OrderService(u).send_940_now(id, rt.outbound, replace=replace, version=version, reason=body.reason if body else None)
    await u.commit()
    return out


@routes.get("/{id}/po.pdf")
async def po_pdf(u: UoW, id: int) -> Response:
    name, pdf = await OrderService(u).po_pdf(id)
    return FileResult(pdf, name, media_type="application/pdf", inline=True)


@routes.get("/{id}/packet.zip")
async def packet(u: UoW, id: int) -> Response:
    name, data = await OrderService(u).packet(id)
    return FileResult(data, name, media_type="application/zip")


@routes.get("/{id}/shipments/{shipment_id}/packing-list.pdf")
async def packing_list(u: UoW, id: int, shipment_id: int) -> Response:
    name, pdf = await OrderService(u).packing_list(id, shipment_id)
    return FileResult(pdf, name, media_type="application/pdf", inline=True)


@routes.post("/shipments/{shipment_id}/shopify-retry")
async def retry_shopify(u: UoW, rt: Rt, shipment_id: int) -> dict:
    out = await OrderService(u).retry_shopify(shipment_id, rt.shipments)
    await u.commit()
    return out


@routes.post("/{order_id}/shopify-fulfil")
async def fulfil_at_997(u: UoW, rt: Rt, order_id: int, version: Version = None) -> dict:
    out = await OrderService(u).fulfil_at_997(order_id, rt.shipments, version)
    await u.commit()
    return out


@routes.post("/shipments/{shipment_id}/rows/{row_id}/resolve", response_model=OrderRead)
async def resolve_945_row(u: UoW, rt: Rt, shipment_id: int, row_id: int, body: ResolveRowIn):
    o = await OrderActions(u, rt.tasks).resolve_row(shipment_id, row_id, body.order_line_id, body.reason)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@routes.post("/manual/preview", response_model=list[LinePreview])
async def manual_preview(u: UoW, lines: list[ManualLineIn]):
    return await ManualOrderService(u).preview(lines)  # reads only


@routes.post("/manual", response_model=OrderRead, status_code=201)
async def manual_create(u: UoW, data: ManualOrderIn):
    o = await ManualOrderService(u).create(data)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@routes.put("/manual/{id}", response_model=OrderRead)
async def manual_update(u: UoW, id: int, data: ManualOrderIn, version: Version = None):
    o = await ManualOrderService(u).update(id, data, version)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@routes.post("/manual/{id}/submit", response_model=OrderRead)
async def manual_submit(u: UoW, id: int, version: Version = None):
    o = await ManualOrderService(u).submit(id, version)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@routes.post("/{id}/reorder", response_model=OrderRead)
async def reorder(u: UoW, id: int):
    """Reorder for this customer (user 2026-11-19): a Draft manual order copied from this one, no lines."""
    o = await ManualOrderService(u).reorder(id)
    await u.commit()
    return await OrderService(u).get(o.id)


@routes.post("/manual/{id}/cancel", response_model=OrderRead)
async def manual_cancel(u: UoW, id: int, version: Version = None):
    o = await ManualOrderService(u).cancel(id, version)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@routes.post("/{id}/settle", response_model=OrderRead)
async def settle_order(u: UoW, id: int, body: SettleIn, version: Version = None):
    o = await OrderPaymentService(u).settle(id, body, version)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@routes.post("/{id}/unsettle", response_model=OrderRead)
async def unsettle_order(u: UoW, id: int, body: UnsettleIn, version: Version = None):
    o = await OrderPaymentService(u).unsettle(id, body.reason, version)
    await u.commit()
    return await OrderService(u).get(o.id)


@routes.put("/{id}/returned", response_model=OrderDetail)
async def set_returned(u: UoW, id: int, body: ReturnedIn, version: Version = None):
    out = await OrderService(u).set_returned(id, body, version)
    await u.commit()
    return out


@routes.put("/{id}/invoice-prices", response_model=OrderDetail)
async def set_invoice_prices(u: UoW, id: int, body: InvoicePricesIn, version: Version = None):
    out = await OrderService(u).set_invoice_prices(id, body, version)
    await u.commit()
    return out


@routes.post("/{id}/resolve-review", response_model=ReviewResult)
async def resolve_review(u: UoW, rt: Rt, id: int):
    out = await OrderService(u).resolve_review(id, rt.intake.tasks, rt.container.shopify)
    await u.commit()
    return out


@routes.post("/{id}/cancel", response_model=OrderRead)
async def cancel_order(u: UoW, rt: Rt, id: int, body: CancelOrderIn, version: Version = None):
    o = await OrderActions(u, rt.tasks).cancel(id, body.reason, version)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@routes.post("/{id}/release-hold", response_model=OrderRead)
async def release_hold(u: UoW, rt: Rt, id: int, body: ReleaseHoldIn, version: Version = None):
    o = await OrderActions(u, rt.tasks).release_hold(id, body.to, body.reason, version)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


router = resource_router(OrderService, uow=get_uow, custom=routes, runtime=get_runtime)
