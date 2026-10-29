"""FastAPI dependency injection: DB session, unit of work, current user."""
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import AsyncSessionLocal, get_session
from app.platform.actors import actor_from_token
from app.platform.uow import SqlAlchemyUnitOfWork, UnitOfWorkFactory

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


# ── unit of work per request (design §13.4) ──────────────────────────────────
# Unlike get_session above (which commits when the request ends), a unit of
# work commits only when the endpoint calls `uow.commit()`: leaving without it
# rolls back. Audit trail and event handlers are attached with the business
# tables (P0).


def make_get_uow(factory: UnitOfWorkFactory):
    async def get_uow(payload: CurrentUserPayload) -> AsyncGenerator[SqlAlchemyUnitOfWork, None]:
        async with factory(actor_from_token(payload)) as uow:
            yield uow
    return get_uow


get_uow = make_get_uow(UnitOfWorkFactory(AsyncSessionLocal))
UoWDep = Annotated[SqlAlchemyUnitOfWork, Depends(get_uow)]
