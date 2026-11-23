"""The request log (≈ ABP's AuditLog, user 2026-11-23): one structured line per HTTP request, on stdout (no table).

    {"event": "request", "request_id", "method", "route" (the template, e.g. /api/v1/orders/{id}), "status",
     "duration_ms", "user_id", "user_name", "role", "client_ip", "error" (a business error's code or an exception's
     type)}

No request body and no query string are written (they carry customer data). Each request has an id: the caller's
X-Request-Id when it is a plain token, else a new one; it is returned in X-Request-Id, written into the oplog's
context (services/oplog.py) and is the correlation id of a 500 (platform/errors.py), so one id finds them all.
The person is noted by the UoW dependency (`note_actor`) into this request's holder.
"""
import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# This request's facts: set by the middleware, filled in by the dependency and the error handlers.
_current: ContextVar[dict[str, Any] | None] = ContextVar("request_facts", default=None)


def current_request_id() -> str | None:
    r = _current.get()
    return r["request_id"] if r else None


def note_actor(actor: Any) -> None:
    r = _current.get()
    if r is not None:
        r.update(user_id=str(actor.user_id) if actor.user_id else None, user_name=actor.name, role=actor.role)


def note_error(code: str) -> None:
    r = _current.get()
    if r is not None:
        r["error"] = code


def current_route() -> str | None:
    """The route template once the request is routed (e.g. for a refusal's security log)."""
    r = _current.get()
    return (r.get("route") or _route_of(r["scope"])) if r else None


def _route_of(scope: Scope) -> str | None:
    endpoint, app = scope.get("endpoint"), scope.get("app")
    for route in getattr(getattr(app, "router", None), "routes", []):
        if getattr(route, "endpoint", None) is endpoint:
            return getattr(route, "path", None)
    return None


def _client_ip(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            return value.decode("latin-1").split(",")[0].strip() or None
    client = scope.get("client")
    return client[0] if client else None


class RequestLogMiddleware:
    """`logger`: the module's request logger's name (e.g. "<module>.request")."""

    def __init__(self, app: ASGIApp, logger: str = "request") -> None:
        self.app = app
        self.logger = logging.getLogger(logger)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        given = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"x-request-id"), "")
        rid = given if _ID.match(given) else uuid.uuid4().hex
        facts: dict[str, Any] = {"request_id": rid, "status": 500, "scope": scope}
        token = _current.set(facts)
        start = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                facts["status"] = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", rid.encode())]
            await send(message)

        failed = False
        try:
            await self.app(scope, receive, send_with_id)
        except Exception as e:
            # Left set: the 500 handler (outside this middleware, same task) gives this id as its correlation id.
            failed = True
            facts.setdefault("error", type(e).__name__)
            raise
        finally:
            facts.setdefault("route", _route_of(scope))
            line = {"event": "request", "request_id": rid, "method": scope.get("method"),
                    "route": facts.get("route") or scope.get("path"), "status": facts["status"],
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    "user_id": facts.get("user_id"), "user_name": facts.get("user_name"), "role": facts.get("role"),
                    "client_ip": _client_ip(scope), "error": facts.get("error")}
            self.logger.info(json.dumps(line, separators=(",", ":")))
            if not failed:
                _current.reset(token)
