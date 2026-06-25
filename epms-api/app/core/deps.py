"""FastAPI dependency injection: DB session, current user, RBAC."""
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
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
    Check that the current user's role has a specific permission enabled
    in the company config's role_permissions matrix.
    system_admin always passes.

    Usage:
        async def endpoint(user: Annotated[dict, Depends(require_permission("invoice_upload"))]):
    """
    async def _check(payload: CurrentUserPayload, db: AsyncSession = Depends(get_session)) -> dict:
        role = payload.get("role", "")
        if role == "system_admin":
            return payload

        from app.models.config import CompanyConfig
        from app.crud.config import get_effective_role_permissions
        result = await db.execute(select(CompanyConfig).limit(1))
        cfg = result.scalar_one_or_none()
        perms = get_effective_role_permissions(cfg) if cfg else {}

        if not perms.get(role, {}).get(permission, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return payload

    return _check
