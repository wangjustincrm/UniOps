"""`settings_router` (user 2026-11-24, stage 4): the two routes of a settings form, from what its group declares
(platform/settings_group.py): GET its values, PUT what changes — the answer is the values as stored.

    edi_settings = settings_router(EdiSettingsService, uow=get_uow)

The router enforces no permission of its own: the group checks the keys it declares. The write commits by itself (platform/routes.py)."""
# No `from __future__ import annotations`: FastAPI must see the closures' real types.
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.platform.settings_group import SettingsGroup
from app.platform.routes import Router


def settings_router(group: type[SettingsGroup], *, uow: Callable[..., Any]) -> APIRouter:
    if not group.path:
        raise TypeError(f"{group.__name__} declares no path")
    r = Router(prefix=group.path, tags=list(group.tags) or [group.path.strip("/")])
    Read, Update = group.read_schema, group.update_schema or group.read_schema
    UoW = Annotated[Any, Depends(uow)]

    @r.get("", response_model=Read)
    async def get(u: UoW):
        return await group(u).get()

    @r.put("", response_model=Read)
    async def update_(u: UoW, body: Update):  # type: ignore[valid-type]
        return await group(u).update(body)

    return r
