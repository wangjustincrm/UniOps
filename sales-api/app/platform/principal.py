"""An active principal (user 2026-11-24, ≈ ABP: an inactive IdentityUser cannot sign in, and a live session ends when
the user changes — dynamic claims / the security stamp): a valid token is not enough; its user must still be active.

- PrincipalStatusProvider (Strategy): where a user's status comes from — the module gives its implementation (e.g.
  the users table, read only); the composition root sets it with `use_principal_status`.
- CachedPrincipalStatus: the provider behind a short TTL cache (AuthenticationOptions.principal_cache_seconds), so a
  deactivation takes effect within it, without a read on every request.
- `ensure_active(user_id)`: called once per request where the token is accepted; an inactive (or missing) user is a
  401 `auth.user_inactive` / `auth.user_missing`, told first to the `on_inactive` listeners (Observer: the security
  log). The system actor and background work never pass here: a job, an outbox send queued by a person, run as they
  were queued.
"""
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.platform.errors import BusinessError
from app.platform.options import AuthenticationOptions, options

log = logging.getLogger(__name__)
ACTIVE, INACTIVE, MISSING = "active", "inactive", "missing"


class PrincipalStatusProvider(Protocol):
    async def status(self, user_id: uuid.UUID) -> str: ...  # ACTIVE | INACTIVE | MISSING


class AllPrincipalsActive:
    """No source configured (a script, a test): every principal counts as active."""
    async def status(self, user_id: uuid.UUID) -> str:  # noqa: ARG002
        return ACTIVE


class CachedPrincipalStatus:
    def __init__(self, provider: PrincipalStatusProvider, clock: Callable[[], float] = time.monotonic) -> None:
        self.provider, self.clock = provider, clock
        self._cache: dict[uuid.UUID, tuple[float, str]] = {}

    async def status(self, user_id: uuid.UUID) -> str:
        now = self.clock()
        hit = self._cache.get(user_id)
        if hit and now - hit[0] < options(AuthenticationOptions).principal_cache_seconds:
            return hit[1]
        value = await self.provider.status(user_id)
        self._cache[user_id] = (now, value)
        return value

    def forget(self, user_id: uuid.UUID | None = None) -> None:
        if user_id is None:
            self._cache.clear()
        else:
            self._cache.pop(user_id, None)


class InactivePrincipal(BusinessError):
    status_code = 401

    def __init__(self, status: str) -> None:
        code = "auth.user_missing" if status == MISSING else "auth.user_inactive"
        super().__init__(code, "this account is not active: ask an administrator" if status == INACTIVE
                         else "this account does not exist any more: ask an administrator")


InactiveListener = Callable[[uuid.UUID, str, Any], Awaitable[None]]
_listeners: list[InactiveListener] = []
_status: Any = AllPrincipalsActive()


def use_principal_status(provider: Any) -> None:
    global _status
    _status = provider


def current_principal_status() -> Any:
    return _status


def on_inactive(listener: InactiveListener) -> InactiveListener:
    if listener not in _listeners:
        _listeners.append(listener)
    return listener


async def ensure_active(user_id: uuid.UUID | None, payload: dict[str, Any] | None = None) -> None:
    """Refuses a token whose user is no longer active (or no longer exists)."""
    if user_id is None:
        return
    state = await _status.status(user_id)
    if state == ACTIVE or (state == MISSING and not options(AuthenticationOptions).refuse_missing_principals):
        return
    for listener in list(_listeners):
        try:
            await listener(user_id, state, payload)
        except Exception:  # noqa: BLE001 — the refusal stands whatever the log does
            log.exception("an inactive-principal listener failed")
    raise InactivePrincipal(state)
