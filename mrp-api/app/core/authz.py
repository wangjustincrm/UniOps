"""This service's authz gate, bound to its own DB/token dependencies.

Import require_permission FROM HERE — the package needs this service's own
get_session / get_current_user_payload dependencies wired in; it deliberately
does not expose a module-level require_permission because those dependency
names differ across services.
"""
from uniops_authz import bind

from app.core.deps import get_current_user_payload, get_session

require_permission = bind(get_session, get_current_user_payload)


def require_any_permission(*keys: str):
    """Admit a caller holding ANY of `keys` (system_admin always).

    The WMS inventory screens moved to the Inventory module (2026-10-14), but
    their data is still served here: they are reachable with MRP's
    mrp.report.view or with inventory.view, and neither audience should need
    the other module's key."""
    from typing import Annotated
    import uuid

    from fastapi import Depends, HTTPException, status
    from sqlalchemy.ext.asyncio import AsyncSession
    from uniops_authz.core import has_permission

    async def _check(
        payload: Annotated[dict, Depends(get_current_user_payload)],
        db: Annotated[AsyncSession, Depends(get_session)],
    ) -> dict:
        role = payload.get("role", "")
        if role == "system_admin":
            return payload
        uid = uuid.UUID(payload["sub"])
        for key in keys:
            if await has_permission(db, uid, role, key):
                return payload
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return _check
