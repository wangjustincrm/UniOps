"""Transfers (P7): list, detail with lines and receipts, create / edit a
Draft, amend, settle a difference; Ready to send, Cancel, Resend and Release
hold are record actions (app/services/transfer_actions.py). Permissions are
checked by the service; each write commits by itself (platform/routes.py)."""
from typing import Annotated, Any

from fastapi import Depends, Query

from app.core.deps import get_uow
from app.platform.crud_router import resource_router
from app.platform.routes import Router
from app.runtime import Runtime, get_runtime
from app.schemas.transfers import AmendIn, ResolveIn, TransferDetail, TransferIn
from app.services.transfer_views import TransferService

UoW = Annotated[Any, Depends(get_uow)]
Rt = Annotated[Runtime, Depends(get_runtime)]
Version = Annotated[int | None, Query()]

routes = Router(prefix="/transfers", tags=["transfers"])


@routes.post("", response_model=TransferDetail, status_code=201)
async def create_transfer(u: UoW, rt: Rt, body: TransferIn):
    return await TransferService(u).create(rt.transfers, body)


@routes.put("/{id}", response_model=TransferDetail)
async def update_transfer(u: UoW, rt: Rt, id: int, body: TransferIn, version: Version = None):
    return await TransferService(u).update(rt.transfers, id, body, version)


@routes.post("/{id}/amend", response_model=TransferDetail)
async def amend(u: UoW, rt: Rt, id: int, body: AmendIn, version: Version = None):
    return await TransferService(u).amend(rt.transfers, id, body, version)


@routes.post("/{id}/resolve", response_model=TransferDetail)
async def resolve(u: UoW, rt: Rt, id: int, body: ResolveIn, version: Version = None):
    return await TransferService(u).resolve(rt.transfers, id, body, version)


router = resource_router(TransferService, uow=get_uow, custom=routes, runtime=get_runtime)
