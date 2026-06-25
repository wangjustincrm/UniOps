"""FastAPI dependencies — DB session, Redis, current-user payload."""
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.base as _db
from app.core.security import decode_token
from app.db.redis import get_redis

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
RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]


def get_token_payload(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> dict:
    try:
        payload = decode_token(creds.credentials)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return payload


CurrentUserPayload = Annotated[dict, Depends(get_token_payload)]
