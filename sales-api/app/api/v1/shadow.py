"""The shadow-run comparison (design §5.4): the list, its counts, one
comparison side by side, and "expected difference". Permissions are checked
by the service; the write commits once."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_uow
from app.platform.crud_router import page_body
from app.platform.query import QuerySpec, query_spec
from app.schemas.shadow import ExpectedIn, ShadowCompare, ShadowMatchRead
from app.services.shadow import ShadowMatchService

UoW = Annotated[Any, Depends(get_uow)]
Spec = Annotated[QuerySpec, Depends(query_spec)]

router = APIRouter(prefix="/shadow-matches", tags=["shadow"])


@router.get("")
async def list_matches(u: UoW, spec: Spec) -> dict:
    return page_body(await ShadowMatchService(u).list(spec))


@router.get("/meta")
async def matches_meta(u: UoW) -> dict:
    return await ShadowMatchService(u).meta()


@router.get("/summary")
async def matches_summary(u: UoW) -> dict[str, int]:
    return await ShadowMatchService(u).summary()


@router.get("/{id}", response_model=ShadowCompare)
async def get_match(u: UoW, id: int):
    return await ShadowMatchService(u).compare_view(id)


@router.post("/{id}/expected", response_model=ShadowMatchRead)
async def mark_expected(u: UoW, id: int, body: ExpectedIn, version: Annotated[int | None, Query()] = None):
    out = await ShadowMatchService(u).mark_expected(id, body.reason, version)
    await u.commit()
    return out
