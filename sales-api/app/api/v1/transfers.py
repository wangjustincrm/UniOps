"""Transfers (P7): list, detail with lines and receipts, create / edit a
Draft, Ready to send, Cancel, Resend. Permissions are checked by the
service; each write commits once."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.core.deps import get_uow
from app.platform.crud_router import page_body
from app.platform.query import QuerySpec, query_spec
from app.runtime import Runtime, get_runtime
from app.schemas.transfers import AmendIn, CancelIn, ReleaseIn, ResolveIn, TransferDetail, TransferIn
from app.services.transfer_views import TransferService

UoW = Annotated[Any, Depends(get_uow)]
Rt = Annotated[Runtime, Depends(get_runtime)]
Spec = Annotated[QuerySpec, Depends(query_spec)]
Version = Annotated[int | None, Query()]

router = APIRouter(prefix="/transfers", tags=["transfers"])


@router.get("")
async def list_transfers(u: UoW, spec: Spec) -> dict:
    return page_body(await TransferService(u).list(spec))


@router.get("/meta")
async def transfers_meta(u: UoW) -> dict:
    return await TransferService(u).meta()


@router.post("", response_model=TransferDetail, status_code=201)
async def create_transfer(u: UoW, rt: Rt, body: TransferIn):
    out = await TransferService(u).create(rt.transfers, body)
    await u.commit()
    return out


@router.get("/{id}/print.pdf")
async def transfer_pdf(u: UoW, id: int) -> Response:
    name, pdf = await TransferService(u).pdf(id)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.get("/{id}", response_model=TransferDetail)
async def get_transfer(u: UoW, id: int):
    return await TransferService(u).detail(id)


@router.put("/{id}", response_model=TransferDetail)
async def update_transfer(u: UoW, rt: Rt, id: int, body: TransferIn, version: Version = None):
    out = await TransferService(u).update(rt.transfers, id, body, version)
    await u.commit()
    return out


@router.post("/{id}/ready", response_model=TransferDetail)
async def ready(u: UoW, rt: Rt, id: int, version: Version = None):
    out = await TransferService(u).ready(rt.transfers, id, version)
    await u.commit()
    return out


@router.post("/{id}/cancel", response_model=TransferDetail)
async def cancel(u: UoW, rt: Rt, id: int, body: CancelIn, version: Version = None):
    out = await TransferService(u).cancel(rt.transfers, id, version, body.reason)
    await u.commit()
    return out


@router.post("/{id}/resend", response_model=TransferDetail)
async def resend(u: UoW, rt: Rt, id: int, version: Version = None):
    out = await TransferService(u).resend(rt.transfers, id, version)
    await u.commit()
    return out


@router.post("/{id}/release", response_model=TransferDetail)
async def release(u: UoW, id: int, body: ReleaseIn, version: Version = None):
    out = await TransferService(u).release(id, body.reason, version)
    await u.commit()
    return out


@router.post("/{id}/amend", response_model=TransferDetail)
async def amend(u: UoW, rt: Rt, id: int, body: AmendIn, version: Version = None):
    out = await TransferService(u).amend(rt.transfers, id, body, version)
    await u.commit()
    return out


@router.post("/{id}/resolve", response_model=TransferDetail)
async def resolve(u: UoW, rt: Rt, id: int, body: ResolveIn, version: Version = None):
    out = await TransferService(u).resolve(rt.transfers, id, body, version)
    await u.commit()
    return out
