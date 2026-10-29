"""FastAPI dependency injection: DB session, unit of work, current user."""
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import AsyncSessionLocal, get_session
from uniops_authz import has_permission

from app.models.records import AuditRecord
from app.platform.actors import Actor, actor_from_token
from app.platform.audit import AuditTrail
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
# work commits only when the endpoint calls `uow.commit()` — exactly once, at
# the end of a write endpoint; leaving without it rolls back. Every flush of
# an Audited row writes sales_audit (FR-OB-53).
#
# Permissions are checked by the services (app/platform/authorization.py)
# through this checker: the UniOps role matrix, the same admission rule as
# packages/authz require_permission (system_admin passes; otherwise the
# user's role union must grant the key).


async def check_permission(session: AsyncSession, actor: Actor, key: str) -> bool:
    if actor.user_id is None or not actor.role:
        return False
    return await has_permission(session, actor.user_id, actor.role, key)


def make_get_uow(factory: UnitOfWorkFactory):
    async def get_uow(payload: CurrentUserPayload) -> AsyncGenerator[SqlAlchemyUnitOfWork, None]:
        async with factory(actor_from_token(payload)) as uow:
            yield uow
    return get_uow


uow_factory = UnitOfWorkFactory(
    AsyncSessionLocal, permissions=check_permission, audit=AuditTrail(AuditRecord),
)


def get_uow_factory() -> UnitOfWorkFactory:
    """The app's factory; tests override this with one bound to a *_test database."""
    return uow_factory


async def get_uow(
    payload: CurrentUserPayload,
    factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
) -> AsyncGenerator[SqlAlchemyUnitOfWork, None]:
    async with factory(actor_from_token(payload)) as uow:
        yield uow


UoWDep = Annotated[SqlAlchemyUnitOfWork, Depends(get_uow)]
