"""`crud_router` (design §13.5): the whole HTTP surface of one config table.

    router = crud_router("/config/partners", PartnerService, uow=get_uow)

list · meta · get · create · update (`?version=`) · deactivate · export.csv ·
import.csv (`?dry_run=`). Deactivate is mounted only for a Deactivatable model,
and the CSV pair only for a service that declares its CSV columns and key: a
route that can only fail is never offered. The router enforces no permission of its own: the
service checks the keys it declares (app/platform/authorization.py), so the
same rule holds for any other caller. `uow` is the business code's
request-scoped unit of work (it authenticates the caller). Each write endpoint
is the outermost caller and commits exactly once, at the end.
"""
# No `from __future__ import annotations`: FastAPI must see the real types of
# the closures' parameters (the service's schemas and the dependencies).
import dataclasses
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from app.platform.entities import Deactivatable
from app.platform.query import Page, QuerySpec, query_spec
from app.platform.services import MAX_IMPORT_BYTES, CrudService, ReadOnlyService


def page_body(page: Page) -> dict:
    """A list response: `{items, total}`, plus `groups` and `groups_truncated`
    when the query grouped (design §13.19), and `totals` when the resource sums fields."""
    body: dict = {"items": [i.model_dump(mode="json") for i in page.items], "total": page.total}
    if page.groups is not None:
        body["groups"] = [g.model_dump(mode="json") for g in page.groups]
        body["groups_truncated"] = page.groups_truncated
    if page.totals is not None:
        body["totals"] = [t.model_dump(mode="json") for t in page.totals]
    return body


def read_router(
    prefix: str,
    service_cls: type[ReadOnlyService],
    *,
    uow: Callable[..., Any],
    tags: list[str] | None = None,
) -> APIRouter:
    """The read half of `crud_router` (list · get) for a ReadOnlyService:
    registers, logs, runs. The service checks its read permission."""
    r = APIRouter(prefix=prefix, tags=tags or [prefix.strip("/")])
    Read = service_cls.read_schema
    UoW = Annotated[Any, Depends(uow)]
    Spec = Annotated[QuerySpec, Depends(query_spec)]

    @r.get("")
    async def list_(u: UoW, spec: Spec) -> dict:
        return page_body(await service_cls(u).list(spec))

    @r.get("/meta")
    async def meta(u: UoW) -> dict:
        return await service_cls(u).meta()

    @r.get("/suggest")
    async def suggest(u: UoW, field: str, q: str = "") -> list[str]:
        return await service_cls(u).suggest(field, q[:100])

    @r.get("/{id}", response_model=Read)
    async def get(u: UoW, id: int):
        return await service_cls(u).get(id)

    return r


def crud_router(
    prefix: str,
    service_cls: type[CrudService],
    *,
    uow: Callable[..., Any],
    tags: list[str] | None = None,
) -> APIRouter:
    r = APIRouter(prefix=prefix, tags=tags or [prefix.strip("/")])
    Read = service_cls.read_schema
    Create = service_cls.create_schema
    Update = service_cls.update_schema
    UoW = Annotated[Any, Depends(uow)]
    Spec = Annotated[QuerySpec, Depends(query_spec)]

    @r.get("")
    async def list_(u: UoW, spec: Spec) -> dict:
        return page_body(await service_cls(u).list(spec))

    @r.get("/meta")
    async def meta(u: UoW) -> dict:
        return await service_cls(u).meta()

    @r.get("/suggest")
    async def suggest(u: UoW, field: str, q: str = "") -> list[str]:
        return await service_cls(u).suggest(field, q[:100])

    if service_cls.csv_columns and service_cls.csv_key:
        _csv_routes(r, prefix, service_cls, UoW, Spec)

    @r.get("/{id}", response_model=Read)
    async def get(u: UoW, id: int):
        return await service_cls(u).get(id)

    @r.post("", response_model=Read, status_code=201)
    async def create(u: UoW, data: Create):  # type: ignore[valid-type]
        out = await service_cls(u).create(data)
        await u.commit()
        return out

    @r.put("/{id}", response_model=Read)
    async def update_(u: UoW, id: int, data: Update, version: Annotated[int | None, Query()] = None):  # type: ignore[valid-type]
        out = await service_cls(u).update(id, data, version)
        await u.commit()
        return out

    if issubclass(service_cls.repo_cls.model, Deactivatable):
        @r.post("/{id}/deactivate", response_model=Read)
        async def deactivate(u: UoW, id: int):
            out = await service_cls(u).deactivate(id)
            await u.commit()
            return out

    return r


def _csv_routes(r: APIRouter, prefix: str, service_cls: type[CrudService], UoW: Any, Spec: Any) -> None:
    @r.get("/export.csv")
    async def export(u: UoW, spec: Spec) -> Response:
        data = await service_cls(u).export_csv(spec)
        name = prefix.strip("/").replace("/", "-") or "export"
        return Response(data, media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})

    @r.post("/import.csv")
    async def import_(u: UoW, file: Annotated[UploadFile, File()], dry_run: bool = True) -> dict:
        # Read one byte past the limit: the service refuses an oversized file
        # without the whole upload ever being held in memory.
        report = await service_cls(u).import_csv(await file.read(MAX_IMPORT_BYTES + 1), dry_run=dry_run)
        if report.ok and not dry_run:
            await u.commit()
        body = dataclasses.asdict(report)
        body["counts"] = report.counts
        return body
