"""FastAPI dependency injection: DB session, current user payload, RBAC."""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import get_session

bearer_scheme = HTTPBearer()

# ── DB session ────────────────────────────────────────────────────────────────

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# ── Auth ──────────────────────────────────────────────────────────────────────


async def get_current_user_payload(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> dict:
    """Validate Bearer token and return its JWT payload."""
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
    """RBAC dependency. system_admin always passes.

    Usage:
        async def endpoint(
            payload: Annotated[dict, Depends(require_roles("auditor", "system_admin"))]
        ): ...

    Most Safety endpoints use the Access Control Matrix dependency in app/core/permissions.py instead; use this only where raw role gating is required.
    """
    async def _check(payload: CurrentUserPayload) -> dict:
        role = payload.get("role")
        if role == "system_admin":
            return payload
        if role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return payload

    return _check
