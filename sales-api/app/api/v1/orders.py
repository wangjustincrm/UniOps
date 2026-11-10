"""Orders (P3): list, detail, the review re-check and manual orders.
Permissions are checked by the services; each write commits once."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.core.deps import get_uow
from app.platform.crud_router import page_body
from app.platform.query import QuerySpec, query_spec
from app.runtime import Runtime, get_runtime
from app.schemas.orders import (
    CancelOrderIn, InvoicePricesIn, LinePreview, ManualLineIn, ManualOrderIn, OrderDetail, OrderFrom945In, OrderFrom945Plan, OrderRead,
    ReleaseHoldIn, ResolveRowIn, ReviewResult,
)
from app.services.order_from_945 import OrderFrom945
from app.services.order_holds import OrderActions
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


@router.get("/{id}/po.pdf")
async def po_pdf(u: UoW, id: int) -> Response:
    name, pdf = await OrderService(u).po_pdf(id)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.get("/{id}/packet.zip")
async def packet(u: UoW, id: int) -> Response:
    name, data = await OrderService(u).packet(id)
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{id}/shipments/{shipment_id}/packing-list.pdf")
async def packing_list(u: UoW, id: int, shipment_id: int) -> Response:
    name, pdf = await OrderService(u).packing_list(id, shipment_id)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.post("/shipments/{shipment_id}/shopify-retry")
async def retry_shopify(u: UoW, rt: Rt, shipment_id: int) -> dict:
    out = await OrderService(u).retry_shopify(shipment_id, rt.shipments)
    await u.commit()
    return out


@router.get("/shipments-without-order", response_model=list[OrderFrom945Plan])
async def shipments_without_order(u: UoW, document_id: int | None = None):
    return await OrderFrom945(u).without_order(document_id)  # reads only


@router.post("/shipments/{shipment_id}/create-order", response_model=OrderRead, status_code=201)
async def create_order_from_945(u: UoW, shipment_id: int, body: OrderFrom945In):
    o = await OrderFrom945(u).create(shipment_id, body.partner_code)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@router.post("/shipments/{shipment_id}/rows/{row_id}/resolve", response_model=OrderRead)
async def resolve_945_row(u: UoW, rt: Rt, shipment_id: int, row_id: int, body: ResolveRowIn):
    o = await OrderActions(u, rt.tasks).resolve_row(shipment_id, row_id, body.order_line_id, body.reason)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


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
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@router.put("/manual/{id}", response_model=OrderRead)
async def manual_update(u: UoW, id: int, data: ManualOrderIn, version: Version = None):
    o = await ManualOrderService(u).update(id, data, version)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@router.post("/manual/{id}/submit", response_model=OrderRead)
async def manual_submit(u: UoW, id: int, version: Version = None):
    o = await ManualOrderService(u).submit(id, version)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@router.post("/manual/{id}/cancel", response_model=OrderRead)
async def manual_cancel(u: UoW, id: int, version: Version = None):
    o = await ManualOrderService(u).cancel(id, version)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@router.get("/{id}", response_model=OrderDetail)
async def order_detail(u: UoW, id: int):
    return await OrderService(u).detail(id)


@router.put("/{id}/invoice-prices", response_model=OrderDetail)
async def set_invoice_prices(u: UoW, id: int, body: InvoicePricesIn):
    out = await OrderService(u).set_invoice_prices(id, body)
    await u.commit()
    return out


@router.post("/{id}/resolve-review", response_model=ReviewResult)
async def resolve_review(u: UoW, rt: Rt, id: int):
    out = await OrderService(u).resolve_review(id, rt.intake.tasks, rt.container.shopify)
    await u.commit()
    return out


@router.post("/{id}/cancel", response_model=OrderRead)
async def cancel_order(u: UoW, rt: Rt, id: int, body: CancelOrderIn, version: Version = None):
    o = await OrderActions(u, rt.tasks).cancel(id, body.reason, version)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts


@router.post("/{id}/release-hold", response_model=OrderRead)
async def release_hold(u: UoW, rt: Rt, id: int, body: ReleaseHoldIn, version: Version = None):
    o = await OrderActions(u, rt.tasks).release_hold(id, body.to, body.reason, version)
    await u.commit()
    return await OrderService(u).get(o.id)  # read again: a flush expires the row's *_sql facts
