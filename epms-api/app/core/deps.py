"""FastAPI dependency injection: DB session, current user, RBAC."""
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.redis import get_redis
from app.db.session import get_session

bearer_scheme = HTTPBearer()

# ── DB session ────────────────────────────────────────────────────────────────

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]

# ── Auth ─────────────────────────────────────────────────────────────────────


async def get_current_user_payload(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> dict:
    """Validates Bearer token and returns its payload."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise credentials_exception

    if payload.get("type") != "access":
        raise credentials_exception

    return payload


CurrentUserPayload = Annotated[dict, Depends(get_current_user_payload)]


async def get_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> str:
    return credentials.credentials

BearerToken = Annotated[str, Depends(get_bearer_token)]


def require_roles(*roles: str):
    """
    Usage:
        @router.get("/admin", dependencies=[Depends(require_roles("system_admin"))])

    Or as a type dep:
        async def endpoint(payload: Annotated[dict, Depends(require_roles("gm", "opm"))]):
    """
    async def _check(payload: CurrentUserPayload) -> dict:
        if payload.get("role") not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return payload

    return _check


def require_permission(permission: str):
    """
    Check that the current user's role has a specific permission enabled,
    reading identity's role_permissions/role_permission_locks directly (same
    physical DB — no HTTP, no cache). system_admin always passes.

    The real implementation lives in app.core.authz, bound to this service's
    own get_session/get_current_user_payload dependencies (see there). It is
    imported lazily here — not at module level — to avoid a circular import:
    app.core.authz imports get_session/get_current_user_payload FROM this
    module, so by the time anything actually calls require_permission(key),
    both modules are guaranteed to be fully loaded.

    Usage:
        async def endpoint(user: Annotated[dict, Depends(require_permission("invoice_upload"))]):
    """
    from app.core.authz import require_permission as _require_permission
    return _require_permission(permission)
