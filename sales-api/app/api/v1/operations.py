"""Connections, the document register, runs and the log (P2). Permissions are
checked by the services; each write endpoint commits once."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from app.core.deps import get_uow
from app.models.config import Connection
from app.platform.crud_router import crud_router, read_router
from app.runtime import Runtime, get_runtime
from app.schemas.audit import AuditEntryRead
from app.schemas.connections import ConnectionRead, ConnectionTestRead, CursorReset, EdiSettings, ShadowSetting
from app.schemas.operations import RunStarted
from app.services import audit
from app.services.connections import READ, ConnectionService, EdiSettingsService, ShadowSettingService
from app.services.operations import DocumentService, LogService, RunService

UoW = Annotated[Any, Depends(get_uow)]
Rt = Annotated[Runtime, Depends(get_runtime)]

connections = crud_router("/config/connections", ConnectionService, uow=get_uow, tags=["connections"])


@connections.get("/{id}/audit", response_model=list[AuditEntryRead])
async def connection_audit(u: UoW, id: int):
    return await audit.history(u, Connection, id, permission=READ)


@connections.post("/{id}/test", response_model=ConnectionTestRead)
async def connection_test(u: UoW, rt: Rt, id: int):
    return await ConnectionService(u).test(id, rt.container.sftp, rt.container.shopify)  # reads only


@connections.post("/{id}/reset-cursor", response_model=ConnectionRead)
async def connection_reset_cursor(u: UoW, id: int, body: CursorReset):
    out = await ConnectionService(u).reset_cursor(id, body.cursor_updated_at)
    await u.commit()
    return out


@connections.post("/{id}/clear-auth-latch", response_model=ConnectionRead)
async def connection_clear_latch(u: UoW, rt: Rt, id: int):
    out = await ConnectionService(u).clear_latch(id, rt.intake.tasks)
    await u.commit()
    return out


documents = read_router("/documents", DocumentService, uow=get_uow, tags=["documents"])


@documents.get("/{id}/raw")
async def document_raw(u: UoW, id: int) -> Response:
    name, content = await DocumentService(u).raw(id)
    safe = name.replace('"', "").replace("\r", "").replace("\n", "")
    return Response(content, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"'})


runs = read_router("/runs", RunService, uow=get_uow, tags=["runs"])


@runs.post("/run-now", response_model=RunStarted, status_code=202)
async def run_now(u: UoW, rt: Rt):
    # Claims its own run record; the request's unit of work only checks the permission.
    return RunStarted(run_id=await RunService(u).run_now(rt.cycle, rt.background))


log = read_router("/log", LogService, uow=get_uow, tags=["log"])


edi_settings = APIRouter(prefix="/config/edi-settings", tags=["connections"])


@edi_settings.get("", response_model=EdiSettings)
async def get_edi_settings(u: UoW):
    return await EdiSettingsService(u).get()


@edi_settings.put("", response_model=EdiSettings)
async def put_edi_settings(u: UoW, body: EdiSettings):
    out = await EdiSettingsService(u).update(body)
    await u.commit()
    return out


shadow_setting = APIRouter(prefix="/config/shadow", tags=["connections"])


@shadow_setting.get("", response_model=ShadowSetting)
async def get_shadow(u: UoW):
    return await ShadowSettingService(u).get()


@shadow_setting.put("", response_model=ShadowSetting)
async def put_shadow(u: UoW, body: ShadowSetting):
    out = await ShadowSettingService(u).update(body)
    await u.commit()
    return out
