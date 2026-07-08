"""Permission checks against the shared EPMS Access Control Matrix.

booking-api reads `company_config.role_permissions` (JSONB) straight from the
shared DB — same read-only raw-SQL approach vms-api uses for SMTP config.
Stored values win; keys missing from a role's stored dict (configs predating
this module) fall back to the local defaults below. system_admin always passes.
"""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUserPayload, SessionDep

_DEFAULTS = {"view_booking": True, "manage_meeting_rooms": False}


def has_permission(role: str, key: str, stored_matrix: dict) -> bool:
    if role == "system_admin":
        return True
    role_perms = stored_matrix.get(role) or {}
    if key in role_perms:
        return bool(role_perms[key])
    return _DEFAULTS.get(key, False)


async def _load_matrix(db: AsyncSession) -> dict:
    row = (
        await db.execute(text("SELECT role_permissions FROM company_config LIMIT 1"))
    ).scalar_one_or_none()
    return row if isinstance(row, dict) else {}


def require_perm(key: str):
    async def _check(payload: CurrentUserPayload, db: SessionDep) -> dict:
        matrix = await _load_matrix(db)
        if not has_permission(payload.get("role", ""), key, matrix):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return payload
    return _check


CurrentUser = Annotated[dict, Depends(require_perm("view_booking"))]
AdminUser = Annotated[dict, Depends(require_perm("manage_meeting_rooms"))]
