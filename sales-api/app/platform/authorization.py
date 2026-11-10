"""Authorization at the service (design §13.5, STEP 1 2026-10-29).

Every application service declares the permission each operation needs and
checks it itself, at the start of the operation — before anything is read
for it or written — so the same rule holds whether the caller is an HTTP
endpoint, a job or another service:

    class PartnerService(CrudService[...]):
        read_permission = "mod.read"
        write_permission = "mod.config"

    class OrderService:
        @requires("mod.operate")
        async def release_hold(self, ...): ...

        @requires("mod.operate", system=True)   # the scheduler may run it
        async def auto_release(self, ...): ...

A person passes when the unit of work's permission checker says so (the
business code wires the UniOps role matrix there). The system actor passes
only where the declaration says `system=True`; everywhere else it is refused
like a person without the key. HTTP routers enforce nothing of their own.
"""
from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.platform.actors import Actor
from app.platform.errors import BusinessError

# (session, actor, permission key) → allowed?
PermissionChecker = Callable[[Any, Actor, str], Awaitable[bool]]

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


class PermissionDenied(BusinessError):
    status_code = 403

    def __init__(self, key: str, actor: Actor) -> None:
        who = "the system actor" if actor.is_system else "you"
        super().__init__("auth.forbidden", f"{who} may not do this (needs {key})", {"permission": key})


async def authorize(uow: Any, key: str, *, system: bool = False) -> None:
    """Raises PermissionDenied unless the unit of work's actor may use `key`."""
    actor: Actor = uow.actor
    if actor.is_system:
        if system:
            return
        raise PermissionDenied(key, actor)
    checker: PermissionChecker | None = getattr(uow, "permissions", None)
    if checker is None:
        raise RuntimeError("no permission checker is wired into the unit of work factory")
    granted: dict[str, bool] | None = getattr(uow, "granted", None)
    if granted is None:
        ok = await checker(uow.session, actor, key)
    elif key in granted:
        ok = granted[key]
    else:
        ok = granted[key] = await checker(uow.session, actor, key)
    if not ok:
        raise PermissionDenied(key, actor)


def requires(key: str, *, system: bool = False) -> Callable[[F], F]:
    """Method decorator for application services (`self.uow` is the unit of
    work): checks `key` before the method body runs."""
    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            await authorize(self.uow, key, system=system)
            return await fn(self, *args, **kwargs)
        wrapper.__requires__ = (key, system)  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]
    return decorate
