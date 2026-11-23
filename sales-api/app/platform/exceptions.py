"""Exception subscribers (≈ ABP's IExceptionSubscriber / ExceptionSubscriber, the Observer pattern, user 2026-11-23):
every exception the error handlers answer is told to each subscriber; a subscriber that fails is logged and ignored.
Each exception is logged at its level (≈ IHasLogLevel): a BusinessError at its `log_level` (WARNING by default, as
ABP's BusinessException), anything else at ERROR. Built in: the audit scope records it (code, message, stack)."""
import logging
import traceback
from dataclasses import dataclass

from app.platform.options import ExceptionHandlingOptions, options

log = logging.getLogger(__name__)


@dataclass
class ExceptionNotification:
    exception: BaseException
    status_code: int
    code: str | None = None
    message: str | None = None
    unexpected: bool = False


class ExceptionSubscriber:
    async def handle(self, n: ExceptionNotification) -> None:  # pragma: no cover — abstract
        raise NotImplementedError


_subscribers: list[ExceptionSubscriber] = []


def add_exception_subscriber(s: ExceptionSubscriber) -> None:
    if all(type(x) is not type(s) for x in _subscribers):
        _subscribers.append(s)


def level_of(exc: BaseException, unexpected: bool) -> int:
    o = options(ExceptionHandlingOptions)
    name = str(getattr(exc, "log_level", None) or (o.unexpected_log_level if unexpected else o.business_log_level)).upper()
    level = logging.getLevelName(name)
    return level if isinstance(level, int) else logging.ERROR


async def notify(n: ExceptionNotification) -> None:
    if not n.unexpected:  # an unexpected one is already logged with its stack by the 500 handler
        log.log(level_of(n.exception, False), "%s %s: %s", n.status_code, n.code or type(n.exception).__name__,
                n.message or str(n.exception))
    for s in list(_subscribers):
        try:
            await s.handle(n)
        except Exception:  # noqa: BLE001 — a subscriber never changes the answer
            log.exception("an exception subscriber failed")


class AuditScopeSubscriber(ExceptionSubscriber):
    """The audit log of the request records the error: a business one by its code and message, an unexpected one by
    its whole stack (≈ ABP's AuditLog.Exceptions)."""

    async def handle(self, n: ExceptionNotification) -> None:
        from app.platform.auditing.scope import current_scope
        scope = current_scope()
        if scope is None:
            return
        if n.unexpected:
            if not scope.log.exceptions:
                scope.add_exception("".join(traceback.format_exception(n.exception)))
        else:
            scope.add_exception(f"{n.code}: {n.message}", code=n.code)


add_exception_subscriber(AuditScopeSubscriber())
