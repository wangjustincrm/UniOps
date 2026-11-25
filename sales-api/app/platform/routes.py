"""A write request commits by itself (≈ ABP's unit of work, stage 5): its endpoint returned → its unit of work is committed, in
the same call, before the response is built and sent; it raised → nothing is kept. A read (GET, HEAD) never commits, whatever
was flushed.

    routes = Router(prefix="/orders", tags=["orders"])

    @routes.post("/{id}/amend")
    async def amend_order(u: UoW, …): return await OrderService(u).amend(…)      # committed once, here

    @routes.post("/manual")
    @own_commit("reads again after the commit")                                    # decides for itself, and says why
    async def manual_create(u: UoW, …): …; await u.commit(); return …

Not in get_uow's teardown: when a `yield` dependency's exit runs — before or after the response is sent — changed between
FastAPI versions, and a commit that fails after the response would have been answered as done. Here a refused commit (a stale
row, a unique name, an event handler in the transaction) is answered by the same error handlers as any other refusal.

The endpoint itself is untouched (its signature, its name, its dependencies): the route calls it. The platform's routers are
Routers; a write route that is none is refused by tests/test_write_routes.py. Services, record actions and job steps never
commit; jobs, `isolated` and the outbox worker commit their own units.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter
from fastapi.routing import APIRoute

from app.platform.uow import SqlAlchemyUnitOfWork

log = logging.getLogger(__name__)
SAFE = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
F = TypeVar("F", bound=Callable[..., Any])


def own_commit(reason: str) -> Callable[[F], F]:
    """The endpoint decides itself whether and when its unit is committed, and why it must."""
    if not reason.strip():
        raise ValueError("say why the route commits for itself")

    def mark(endpoint: F) -> F:
        endpoint.__own_commit__ = reason  # type: ignore[attr-defined]
        return endpoint
    return mark


class UnitOfWorkRoute(APIRoute):
    @property
    def commits(self) -> bool:
        """Whether the route commits for its endpoint: a write that does not decide for itself."""
        return bool(self.methods - SAFE) and not getattr(self.endpoint, "__own_commit__", None)

    def get_route_handler(self) -> Callable[..., Any]:
        if self.commits:
            if not asyncio.iscoroutinefunction(self.endpoint):
                raise TypeError(f"{self.path}: a write endpoint is async")
            self.dependant.call = self._run  # what FastAPI calls; the endpoint stays the route's own (an included route wraps once)
        return super().get_route_handler()

    async def _run(self, **values: Any) -> Any:
        units = [v for v in values.values() if isinstance(v, SqlAlchemyUnitOfWork)]
        if len(units) != 1:  # before anything runs: what it would write could not be kept
            raise RuntimeError(f"{self.path}: a write endpoint takes one unit of work, this one {len(units)}")
        out = await self.endpoint(**values)
        if units[0].committed:
            log.error("%s committed itself without saying so (own_commit)", self.path)
            return out
        await units[0].commit()
        return out


class Router(APIRouter):
    """An APIRouter whose writes commit by themselves."""
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("route_class", UnitOfWorkRoute)
        super().__init__(*args, **kwargs)
