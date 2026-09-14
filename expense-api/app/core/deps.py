"""FastAPI dependencies — auth, DB session."""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
import app.db.base as _db

_bearer = HTTPBearer(auto_error=True)


async def get_db():
    async with _db.AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_db)]


def _decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    # identity-api signs access AND refresh tokens with the SAME secret and
    # separates them only by this claim (core/security.py: {"type": "access"}
    # vs {"type": "refresh"}). Without the check a refresh token — longer-lived,
    # and held somewhere a session token is not — authenticated every endpoint
    # in this service. It carries no `role`, so role gates read "", but `sub` is
    # valid and _user_role_codes still resolves additional roles from user_roles.
    # epms-api and file-api have always checked this; expense-api had not.
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    return payload


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> dict:
    return _decode_token(creds.credentials)


CurrentUserDep = Annotated[dict, Depends(get_current_user)]


def require_roles(*roles: str):
    async def _check(user: CurrentUserDep) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user
    return _check


def get_bearer_token(creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)]) -> str:
    return creds.credentials


BearerTokenDep = Annotated[str, Depends(get_bearer_token)]
