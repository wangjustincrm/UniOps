"""Invoicing (P6): the invoice list and detail, delivery confirmation (one
and in bulk), edit, send / resend, release a hold, and a cancellation or
return after shipment. Permissions are checked by the service; each write
commits once."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.core.deps import get_uow
from app.platform.crud_router import resource_router
from app.runtime import Runtime, get_runtime
from app.schemas.invoices import (
    ConfirmBulkIn, ConfirmIn, ConfirmResult, InvoiceDetail, InvoiceEdit, ReleaseIn, ReturnIn,
)
from app.services.invoice_views import InvoiceService

UoW = Annotated[Any, Depends(get_uow)]
Rt = Annotated[Runtime, Depends(get_runtime)]

routes = APIRouter(prefix="/invoices", tags=["invoices"])


@routes.get("/{id}/invoice.pdf")
async def invoice_pdf(u: UoW, id: int) -> Response:
    name, pdf = await InvoiceService(u).pdf(id)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@routes.post("/confirm", response_model=InvoiceDetail)
async def confirm(u: UoW, rt: Rt, body: ConfirmIn):
    out = await InvoiceService(u).confirm(rt.invoicing, body)
    await u.commit()
    return out


@routes.post("/confirm-bulk", response_model=list[ConfirmResult])
async def confirm_bulk(u: UoW, rt: Rt, body: ConfirmBulkIn):
    out = await InvoiceService(u).confirm_bulk(rt.invoicing, body)
    await u.commit()
    return out


@routes.post("/release")
async def release(u: UoW, rt: Rt, body: ReleaseIn) -> dict:
    out = await InvoiceService(u).release(rt.invoicing, body)
    await u.commit()
    return out


@routes.post("/returns")
async def record_return(u: UoW, rt: Rt, body: ReturnIn) -> dict:
    out = await InvoiceService(u).record_return(rt.invoicing, body)
    await u.commit()
    return out


@routes.put("/{id}", response_model=InvoiceDetail)
async def edit_invoice(u: UoW, rt: Rt, id: int, body: InvoiceEdit, version: Annotated[int | None, Query()] = None):
    out = await InvoiceService(u).edit(rt.invoicing, id, body, version)
    await u.commit()
    return out


@routes.post("/{id}/send", response_model=InvoiceDetail)
async def send_invoice(u: UoW, rt: Rt, id: int):
    out = await InvoiceService(u).send(rt.invoicing, id)
    await u.commit()
    return out


router = resource_router(InvoiceService, uow=get_uow, custom=routes)
