"""FastAPI dependency injection: DB session, current user."""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import get_session

bearer_scheme = HTTPBearer()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user_payload(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> dict:
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
