"""Admin endpoints for BookingConfig (module-level settings)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.deps import SessionDep
from app.core.permissions import AdminUser
from app.crud.config import get_or_create_config
from app.schemas.config import ConfigOut, ConfigUpdate

router = APIRouter()


@router.get("", response_model=ConfigOut)
async def get_config(db: SessionDep, _: AdminUser) -> Any:
    config = await get_or_create_config(db)
    return config


@router.put("", response_model=ConfigOut)
async def update_config(
    data: ConfigUpdate,
    db: SessionDep,
    _: AdminUser,
) -> Any:
    config = await get_or_create_config(db)
    if data.smtp_settings is not None:
        config.smtp_settings = data.smtp_settings
    if data.rules is not None:
        config.rules = data.rules
    if data.organizer_mode is not None:
        config.organizer_mode = data.organizer_mode
    await db.flush()
    await db.refresh(config)
    return config
