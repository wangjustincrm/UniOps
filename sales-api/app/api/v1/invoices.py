"""Invoicing (P6): the invoice list and detail, delivery confirmation of one order,
edit, release a hold, and a cancellation or return after shipment; Send and Send
again are record actions (app/services/invoice_actions.py), as is confirming many
(order_actions.py). Permissions are checked by the service; each write commits by itself (platform/routes.py)."""
from typing import Annotated, Any

from fastapi import Depends, Query

from app.core.deps import get_uow
from app.platform.crud_router import resource_router
from app.platform.routes import Router
from app.runtime import Runtime, get_runtime
from app.schemas.invoices import (
    ConfirmIn, InvoiceDetail, InvoiceEdit, ReleaseIn, ReturnIn,
)
from app.services.invoice_views import InvoiceService

UoW = Annotated[Any, Depends(get_uow)]
Rt = Annotated[Runtime, Depends(get_runtime)]

routes = Router(prefix="/invoices", tags=["invoices"])


@routes.post("/confirm", response_model=InvoiceDetail)
async def confirm(u: UoW, rt: Rt, body: ConfirmIn):
    return await InvoiceService(u).confirm(rt.invoicing, body)


@routes.post("/release")
async def release(u: UoW, rt: Rt, body: ReleaseIn) -> dict:
    return await InvoiceService(u).release(rt.invoicing, body)


@routes.post("/returns")
async def record_return(u: UoW, rt: Rt, body: ReturnIn) -> dict:
    return await InvoiceService(u).record_return(rt.invoicing, body)


@routes.put("/{id}", response_model=InvoiceDetail)
async def edit_invoice(u: UoW, rt: Rt, id: int, body: InvoiceEdit, version: Annotated[int | None, Query()] = None):
    return await InvoiceService(u).edit(rt.invoicing, id, body, version)


router = resource_router(InvoiceService, uow=get_uow, custom=routes, runtime=get_runtime)
