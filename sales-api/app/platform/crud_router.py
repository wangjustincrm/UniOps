"""`resource_router` (design §13.5; user 2026-11-24): the whole HTTP surface of one list, from what its service
declares (platform/resource.py).

    router = resource_router(PartnerService, uow=get_uow)

list · meta · suggest · export.csv · actions · history · get, and for a CrudService create · update (`?version=`) · deactivate ·
import.csv (`?dry_run=`). Deactivate is mounted only for a Deactivatable model, and the CSV import only for a service
that declares its CSV columns and key: a route that can only fail is never offered. The router enforces no permission
of its own: the service checks the keys it declares (app/platform/authorization.py), so the same rule holds for any
other caller. `uow` is the business code's request-scoped unit of work (it authenticates the caller). Each write
endpoint is the outermost caller and commits exactly once, at the end.
"""
# No `from __future__ import annotations`: FastAPI must see the real types of
# the closures' parameters (the service's schemas and the dependencies).
import dataclasses
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from app.platform.audit import AuditEntryRead
from app.platform.bulk_actions import action_router
from app.platform.entities import Deactivatable
from app.platform.files import FileResult
from app.platform.query import Page, QuerySpec, export_columns, query_spec
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


def resource_router(
    service_cls: type[ReadOnlyService],
    *,
    uow: Callable[..., Any],
    custom: APIRouter | None = None,
) -> APIRouter:
    """Every route the service's `resource` declares (platform/resource.py), and for a CrudService its writes. The
    fixed paths come first, then `custom` (the list's hand-written routes, on a router of the same prefix), and
    /{id} last: a fixed path is never taken for an id. The service checks its permissions."""
    res = service_cls.resolved()
    if not res.path:
        raise TypeError(f"{service_cls.__name__} declares no resource path")
    r = APIRouter(prefix=res.path, tags=list(res.tags) or [res.path.strip("/")])
    Read = service_cls.read_schema
    UoW = Annotated[Any, Depends(uow)]
    Spec = Annotated[QuerySpec, Depends(query_spec)]

    @r.get("")
    async def list_(u: UoW, spec: Spec) -> dict:
        return page_body(await service_cls(u).list(spec))

    @r.get("/meta")
    async def meta(u: UoW) -> dict:
        return await service_cls(u).meta()

    if res.suggest:
        @r.get("/suggest")
        async def suggest(u: UoW, field: str, q: str = "") -> list[str]:
            return await service_cls(u).suggest(field, q[:100])

    if res.export:
        _export_route(r, service_cls, UoW, Spec)
    crud = issubclass(service_cls, CrudService)
    if crud and service_cls.csv_columns and service_cls.csv_key:
        _import_route(r, service_cls, UoW)
    if (registry := res.action_registry()) is not None:
        r.include_router(action_router(registry, uow))
    if res.history:
        @r.get("/{id}/audit", response_model=list[AuditEntryRead])
        async def history(u: UoW, id: int):
            return await service_cls(u).history(id)

    if custom is not None:
        if custom.prefix != r.prefix:
            raise ValueError(f"the custom routes of {res.path} are under {custom.prefix!r}")
        r.routes.extend(custom.routes)

    if res.get and res.detail is not None:
        @r.get("/{id}", response_model=res.detail)
        async def detail(u: UoW, id: int):
            return await service_cls(u).detail(id)  # type: ignore[attr-defined]
    elif res.get:
        @r.get("/{id}", response_model=Read)
        async def get(u: UoW, id: int):
            return await service_cls(u).get(id)
    if crud:
        _write_routes(r, service_cls, UoW)
    return r


def _write_routes(r: APIRouter, service_cls: type[CrudService], UoW: Any) -> None:
    Read, Create, Update = service_cls.read_schema, service_cls.create_schema, service_cls.update_schema

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


def _export_route(r: APIRouter, service_cls: type[ReadOnlyService], UoW: Any, Spec: Any) -> None:
    @r.get("/export.csv")
    async def export(u: UoW, spec: Spec, columns: Annotated[list[str] | None, Depends(export_columns)] = None) -> Response:
        data = await service_cls(u).export_csv(spec, columns)
        return FileResult(data, service_cls.resolved().file_name, media_type="text/csv")


def _import_route(r: APIRouter, service_cls: type[CrudService], UoW: Any) -> None:
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
