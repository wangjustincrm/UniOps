"""Permission checks against the shared Access Control Matrix.

booking-api reads identity's role_permissions / role_permission_locks tables
directly via the shared uniops_authz package — same physical DB, no HTTP —
exactly like epms/finance/budget/mdm (see app/core/authz.py). Role union
(primary role ∪ additional user_roles); system_admin always short-circuits.

Behaviour change from the old company_config.role_permissions gate this
replaces: that gate was PRIMARY-role-only with a local `_DEFAULTS` fallback
for keys missing from the stored matrix. This package does role UNION
(primary ∪ additional roles) and has NO fallback — view_booking /
manage_meeting_rooms have had seeded matrix rows since phase 1, so the only
intended behavioural delta is the union broadening, consistent with every
other migrated service.
"""
import uuid
from typing import Annotated

from fastapi import Depends
from uniops_authz import has_permission as _has_permission

from app.core.authz import require_permission
from app.core.deps import CurrentUserPayload, SessionDep


async def is_booking_admin(payload: CurrentUserPayload, db: SessionDep) -> bool:
    """Return True if the caller holds the manage_meeting_rooms permission.

    Crud-layer check (not an endpoint dependency, so it can't take a FastAPI
    Depends(require_permission(...))) — calls the shared authz package's
    has_permission() directly, which carries the same system_admin
    short-circuit and role-union semantics require_permission() enforces.
    """
    uid = uuid.UUID(str(payload.get("sub", "")))
    return await _has_permission(db, uid, payload.get("role", ""), "manage_meeting_rooms")


CurrentUser = Annotated[dict, Depends(require_permission("view_booking"))]
AdminUser = Annotated[dict, Depends(require_permission("manage_meeting_rooms"))]
