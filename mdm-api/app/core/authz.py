"""This service's authz gate, bound to its own DB/token dependencies.

Import require_permission FROM HERE — the package needs this service's own
get_db / get_token_payload dependencies wired in; it deliberately does not
expose a module-level require_permission because those dependency names
differ across services.
"""
import uuid
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from uniops_authz import bind, has_permission

from app.core.deps import get_token_payload
from app.db.base import get_db

require_permission = bind(get_db, get_token_payload)


def require_any_permission(*keys: str) -> Callable:
    """Grant access if the caller holds ANY of the given permission keys
    (system_admin always bypasses, same short-circuit as require_permission /
    has_permission — see uniops_authz.core).

    Used where a write path legitimately has two valid gates that must both
    keep working: e.g. BOM/material-supplier writes are gated on the broad,
    pre-existing `data_maintenance` key (which admins may already hold —
    this endpoint must not silently start 403ing them) OR the narrower
    `mdm.bom.write` key (seeded for MRP phase-0 but, as of the seed script,
    not granted to any role by default — see identity-api/scripts/
    seed_authz.py). Composing this from uniops_authz.has_permission (already
    exported for exactly this "boolean gate outside a Depends()" use case)
    instead of hand-rolling a second copy of the matrix lookup.
    """
    async def _check(
        payload: Annotated[dict, Depends(get_token_payload)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> dict:
        role = payload.get("role", "")
        if role == "system_admin":
            return payload
        uid = uuid.UUID(payload["sub"])
        for key in keys:
            if await has_permission(db, uid, role, key):
                return payload
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )
    return _check
