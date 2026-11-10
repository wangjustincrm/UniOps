"""Unit of work (design §13.4).

One per HTTP request and one per scheduler unit (one connection, one
document, one order). Nothing is committed unless `commit()` is called;
leaving the block without it, or with an exception, rolls back.

    async with uow_factory(actor) as uow:
        ...
        await uow.commit()     # in-transaction event handlers, then COMMIT
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.actors import Actor
from app.platform.audit import AuditTrail
from app.platform.authorization import PermissionChecker
from app.platform.events import EventBuffer, HandlerRegistry


class UnitOfWork(Protocol):
    session: AsyncSession
    events: EventBuffer
    actor: Actor

    async def __aenter__(self) -> UnitOfWork: ...
    async def commit(self) -> None: ...
    async def __aexit__(self, *exc: Any) -> None: ...


class SqlAlchemyUnitOfWork:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        actor: Actor,
        *,
        handlers: HandlerRegistry | None = None,
        audit: AuditTrail | None = None,
        reason: str | None = None,
        permissions: PermissionChecker | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.permissions = permissions
        # Each permission asked once per unit of work (a request): the answer is the same for its life.
        self.granted: dict[str, bool] = {}
        self.actor = actor
        self.handlers = handlers or HandlerRegistry()
        self.audit = audit
        self.reason = reason
        self.events = EventBuffer()
        self.committed = False
        self.session: AsyncSession

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self._session_factory()
        self.session.info["actor"] = self.actor
        self.session.info["audit_reason"] = self.reason
        if self.audit is not None:
            self.audit.attach(self.session.sync_session)
        return self

    def set_reason(self, reason: str | None) -> None:
        """The reason recorded on audit rows flushed from now on."""
        self.session.info["audit_reason"] = reason

    async def commit(self) -> None:
        await self.handlers.dispatch(self.events, self)
        await self.session.commit()
        self.committed = True

    async def rollback(self) -> None:
        self.events.drain()
        await self.session.rollback()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # close() ends an uncommitted transaction with a rollback too, but
        # keeps loaded rows readable — a read-only unit may hand them back.
        # An explicit rollback (on error) expires them, as it should.
        try:
            if exc_type is not None:
                await self.rollback()
            else:
                self.events.drain()
        finally:
            await self.session.close()


class UnitOfWorkFactory:
    """What the composition root hands to services and jobs: one call per unit."""

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        *,
        handlers: HandlerRegistry | None = None,
        audit: AuditTrail | None = None,
        permissions: PermissionChecker | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.handlers = handlers or HandlerRegistry()
        self.audit = audit
        self.permissions = permissions

    def __call__(self, actor: Actor, *, reason: str | None = None) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(
            self.session_factory, actor, handlers=self.handlers, audit=self.audit, reason=reason,
            permissions=self.permissions,
        )
