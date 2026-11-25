"""Connections, the document register, runs and the log (P2). Permissions are
checked by the services; each write endpoint commits once."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from pydantic import ConfigDict, BaseModel, Field

from app.core.config import settings
from app.core.deps import get_uow
from app.platform.crud_router import resource_router
from app.platform.files import FileResult
from app.platform.settings_router import settings_router
from app.runtime import Runtime, get_runtime
from app.schemas.connections import (
    ConnectionRead, ConnectionTestRead, CursorReset,
)
from app.schemas.operations import RunStarted
from app.services.connections import (
    ConnectionService, EdiSettingsService, SchedulerSettingsService,
)
from app.services.order_settings import ManualOrderSettingsService
from app.services.operations import DocumentService, LogService, RunService

UoW = Annotated[Any, Depends(get_uow)]
Rt = Annotated[Runtime, Depends(get_runtime)]

connections = resource_router(ConnectionService, uow=get_uow)


@connections.post("/{id}/test", response_model=ConnectionTestRead)
async def connection_test(u: UoW, rt: Rt, id: int):
    return await ConnectionService(u).test(id, rt.container.sftp, rt.container.shopify, rt.container.authorize_net,
                                               rt.container.paypal)  # reads only


@connections.post("/{id}/run-now", response_model=RunStarted, status_code=202)
async def connection_run_now(u: UoW, rt: Rt, id: int):
    # Claims its own run record (single flight per connection); runs in the background.
    return RunStarted(run_id=await ConnectionService(u).run_now(id, rt.scheduler, rt.background))


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


documents = resource_router(DocumentService, uow=get_uow, runtime=get_runtime)


@documents.get("/{id}/raw")
async def document_raw(u: UoW, id: int) -> Response:
    name, content = await DocumentService(u).raw(id)
    return FileResult(content, name, media_type="application/octet-stream")


runs = resource_router(RunService, uow=get_uow)


@runs.post("/run-now", response_model=RunStarted, status_code=202)
async def run_now(u: UoW, rt: Rt):
    # Claims its own run record; the request's unit of work only checks the permission.
    return RunStarted(run_id=await RunService(u).run_now(rt.scheduler, rt.background))


@runs.post("/run-now/system", response_model=RunStarted, status_code=202)
async def run_system_now(u: UoW, rt: Rt):
    return RunStarted(run_id=await RunService(u).run_system_now(rt.scheduler, rt.background))


log = resource_router(LogService, uow=get_uow)

# Settings forms: their routes from what each group declares (platform/settings_router.py).
scheduler_setting = settings_router(SchedulerSettingsService, uow=get_uow)
edi_settings = settings_router(EdiSettingsService, uow=get_uow)
manual_order_settings = settings_router(ManualOrderSettingsService, uow=get_uow)


# Health → Log levels (user 2026-11-23): raise a logger for a while; it goes back by itself.
log_levels = APIRouter(prefix="/config/log-levels", tags=["settings"])


class LogLevelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    logger: str = Field("", max_length=100)
    level: str
    minutes: int | None = None  # default 60; 1 to 1440 (it always expires)
    reason: str = Field(min_length=3, max_length=500)


class LogLevelClearIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    logger: str = Field("", max_length=100)
    reason: str = Field(min_length=3, max_length=500)


@log_levels.get("")
async def get_log_levels(u: UoW) -> dict:
    from app.services.log_levels import LogLevelService
    return await LogLevelService(u, settings).get()


@log_levels.post("")
async def raise_log_level(u: UoW, body: LogLevelIn) -> dict:
    from app.services.log_levels import LogLevelService
    out = await LogLevelService(u, settings).raise_level(body.logger, body.level.upper(), body.minutes, body.reason)
    await u.commit()
    return out


@log_levels.post("/clear")
async def clear_log_level(u: UoW, body: LogLevelClearIn) -> dict:
    from app.services.log_levels import LogLevelService
    out = await LogLevelService(u, settings).clear(body.logger, body.reason)
    await u.commit()
    return out


# Health → Switches (user 2026-11-23): partner writes, external email, the scheduler — a person may turn one off.
features = APIRouter(prefix="/config/features", tags=["settings"])


class FeatureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=100)
    value: bool | None = None  # False: off; None / True: back to the server's value (it is the ceiling)
    reason: str = Field(min_length=3, max_length=500)


@features.get("")
async def get_features(u: UoW) -> list[dict]:
    from app.services.features import FeatureService
    return await FeatureService(u, settings).get()


@features.post("")
async def set_feature(u: UoW, body: FeatureIn) -> list[dict]:
    from app.services.features import FeatureService
    out = await FeatureService(u, settings).set(body.name, body.value, body.reason)
    await u.commit()
    return out
