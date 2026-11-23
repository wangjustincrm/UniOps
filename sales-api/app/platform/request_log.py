"""The request log's names since 2026-11-23, kept for their callers: the request log is now the auditing middleware's
stdout line (platform/auditing), the request id the correlation id (platform/correlation.py). Same behaviour."""
from typing import Any

from app.platform import correlation
from app.platform.auditing.middleware import AuditingMiddleware as RequestLogMiddleware
from app.platform.auditing.middleware import route_of
from app.platform.auditing.scope import current_scope


def current_request_id() -> str | None:
    return correlation.current()


def note_actor(actor: Any) -> None:
    scope = current_scope()
    if scope is not None:
        scope.set_actor(actor)


def note_error(code: str) -> None:
    scope = current_scope()
    if scope is not None and not scope.log.error_code:
        scope.log.error_code = code


def current_route() -> str | None:
    """The route template once the request is routed (e.g. for a refusal's security log)."""
    scope = current_scope()
    if scope is None or scope.request_scope is None:
        return None
    return scope.log.route or route_of(scope.request_scope)


__all__ = ["RequestLogMiddleware", "current_request_id", "current_route", "note_actor", "note_error"]
