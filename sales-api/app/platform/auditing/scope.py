"""The audit log of the work running now (≈ ABP's AuditLogInfo + IAuditingManager.BeginScope / Current, user
2026-11-23). A request, a job run, an outbox send: each runs inside `begin_scope(...)`, which

- makes it the ambient scope (a contextvar — the Context Object pattern) and its correlation id the ambient one;
- lets the pieces note into it: the person (`set_actor`), the service actions (`add_action`, from @requires), the
  entity changes (`entity_changed`, from the audit trail), the error (`add_exception`, from the exception subscribers);
- at its end runs the contributors and asks the options whether to save it; the store saves it.

Its id is a UUID made here (as ABP's Guid keys), so an entity-change row is stamped with it while the work runs.
"""
import fnmatch
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.platform import correlation
from app.platform.options import AuditingOptions, options

log = logging.getLogger(__name__)
KINDS = ("request", "job", "outbox", "intake", "cli")


@dataclass
class AuditLogAction:
    """≈ AuditLogActionInfo: a service method that ran (parameters whole: the user's rule "remove nothing")."""
    service_name: str
    method_name: str
    parameters: Any = None
    execution_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    execution_duration_ms: int | None = None


@dataclass
class AuditLogInfo:
    """≈ AuditLogInfo: one per request / background scope."""
    kind: str
    name: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    correlation_id: str = ""
    application_name: str = ""
    user_id: str | None = None
    user_name: str | None = None
    role: str | None = None
    client_ip: str | None = None
    browser_info: str | None = None
    http_method: str | None = None
    url: str | None = None
    route: str | None = None
    http_status: int | None = None
    execution_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    execution_duration_ms: int | None = None
    error_code: str | None = None
    exceptions: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    actions: list[AuditLogAction] = field(default_factory=list)
    entity_changes: int = 0
    forced: bool | None = None  # @audited / @disable_auditing on the endpoint


class AuditScope:
    def __init__(self, info: AuditLogInfo, request_scope: dict | None = None) -> None:
        self.log = info
        self.request_scope = request_scope  # the ASGI scope (a request): its route is found once routed
        self.parameters: Any = None         # a request's body (JSON), given to its actions
        self.duration_exact: float | None = None  # ms, for the stdout line
        self.error_type: str | None = None  # an unexpected exception's type (the stdout line's "error")
        self.stdout: Any = None             # (logger, line) of a request, written when the scope ends

    def set_actor(self, actor: Any) -> None:
        self.log.user_id = str(actor.user_id) if getattr(actor, "user_id", None) else None
        self.log.user_name, self.log.role = getattr(actor, "name", None), getattr(actor, "role", None)

    def add_action(self, service_name: str, method_name: str) -> None:
        self.log.actions.append(AuditLogAction(service_name, method_name, self.parameters))

    def add_exception(self, text: str, code: str | None = None) -> None:
        if code and not self.log.error_code:
            self.log.error_code = code
        self.log.exceptions.append(text)

    def entity_changed(self, n: int = 1) -> None:
        self.log.entity_changes += n


_current: ContextVar[AuditScope | None] = ContextVar("audit_scope", default=None)


def current_scope() -> AuditScope | None:
    return _current.get()


def _matches(route: str | None, patterns: tuple[str, ...]) -> bool:
    return bool(route) and any(fnmatch.fnmatchcase(route, p) for p in patterns)


def should_save(info: AuditLogInfo, o: AuditingOptions | None = None) -> bool:
    """ABP's rules (AuditingHelper.ShouldSaveAudit), with our background scopes and the user's routes."""
    o = o or options(AuditingOptions)
    if not o.is_enabled or info.forced is False:
        return False
    if info.kind != "request":
        return o.save_background_scopes and bool(info.entity_changes or info.actions or info.exceptions)
    if _matches(info.route, o.ignored_routes):
        return False
    if info.forced:
        return True
    if not info.user_id and not o.is_enabled_for_anonymous_users:
        return False
    failed = bool(info.exceptions) or (info.http_status or 0) >= 400
    if failed and o.always_log_on_exception:
        return True
    if _matches(info.route, o.always_log_routes):
        return True
    if (info.http_method or "").upper() in ("GET", "HEAD"):
        return o.is_enabled_for_get_requests
    return True


@asynccontextmanager
async def begin_scope(kind: str, name: str | None = None, *, correlation_id: str | None = None,
                      request_scope: dict | None = None, store: Any = None) -> AsyncIterator[AuditScope]:
    """≈ IAuditingManager.BeginScope + SaveAsync in `finally`: always saved when the options say, also on failure."""
    from app.platform.auditing.contributors import contributors
    from app.platform.auditing.store import current_store
    cid = correlation_id or correlation.current() or correlation.new_id()
    info = AuditLogInfo(kind=kind, name=name, correlation_id=cid, application_name=options(AuditingOptions).application_name)
    scope = AuditScope(info, request_scope)
    token, ctoken = _current.set(scope), correlation.set_current(cid)
    start = time.perf_counter()
    for c in contributors():
        c.pre_contribute(scope)
    failed = False
    try:
        yield scope
    except BaseException as e:
        failed = True
        if not info.exceptions:
            import traceback
            info.exceptions.append("".join(traceback.format_exception(e)))
        raise
    finally:
        scope.duration_exact = (time.perf_counter() - start) * 1000
        info.execution_duration_ms = int(scope.duration_exact)
        for c in contributors():
            c.post_contribute(scope)
        if should_save(info):
            try:
                await (store or current_store()).save(info)
            except Exception:  # noqa: BLE001 — HideErrors: a failed save never fails the work
                if not options(AuditingOptions).hide_errors:
                    raise
                log.exception("could not save the audit log %s", info.id)
        _current.reset(token)
        if not failed:  # left set on a failure: the 500 handler (outside, same task) reads the id
            correlation._current.reset(ctoken)
