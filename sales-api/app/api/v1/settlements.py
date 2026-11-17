"""`/settlements`: the deposits the payment gateways paid out (read-only; CSV; PDFs)."""
from typing import Annotated, Any

from fastapi import Depends, Response

from app.core.deps import get_uow
from app.platform.crud_router import read_router
from app.platform.query import QuerySpec, query_spec
from app.services.settlement_list import SettlementRowService

router = read_router("/settlements", SettlementRowService, uow=get_uow, tags=["settlements"], export=True)
UoW = Annotated[Any, Depends(get_uow)]
Spec = Annotated[QuerySpec, Depends(query_spec)]


def _pdf(name: str, data: bytes) -> Response:
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


# Two segments: never taken for the list's "/{id}".
@router.get("/report/settlements.pdf")
async def settlements_report(u: UoW, spec: Spec) -> Response:
    return _pdf(*await SettlementRowService(u).report_pdf(spec))


@router.get("/{id}/statement.pdf")
async def settlement_statement(u: UoW, id: int) -> Response:
    return _pdf(*await SettlementRowService(u).statement_pdf(id))
