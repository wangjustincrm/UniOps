"""Lots (traceability and recall): list, meta, CSV. Read-only; the service
checks the read permission."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from app.core.deps import get_uow
from app.platform.crud_router import page_body
from app.platform.query import QuerySpec, query_spec
from app.services.lots import LotService

UoW = Annotated[Any, Depends(get_uow)]
Spec = Annotated[QuerySpec, Depends(query_spec)]

router = APIRouter(prefix="/lots", tags=["lots"])


@router.get("")
async def list_lots(u: UoW, spec: Spec) -> dict:
    return page_body(await LotService(u).list(spec))


@router.get("/meta")
async def lots_meta(u: UoW) -> dict:
    return await LotService(u).meta()


@router.get("/export.csv")
async def export_lots(u: UoW, spec: Spec) -> Response:
    return Response(await LotService(u).export_csv(spec), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="lots.csv"'})
