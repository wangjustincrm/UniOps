"""The auditing middleware (≈ ABP's AbpAuditingMiddleware, user 2026-11-23): every HTTP request runs in an audit scope.

- the correlation id: the caller's (X-Correlation-Id / X-Request-Id, a plain token) or a new one, returned in both;
- one JSON line per request on stdout, as the request log wrote it since 2026-11-23 (the same keys);
- the request's body (non-GET, JSON) becomes its actions' parameters, whole; an upload is named by type and size;
- the options decide whether the scope is saved (non-GET, failures, the always-logged routes), by the store.
"""
import json
import logging
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.platform import correlation
from app.platform.auditing.scope import _matches, begin_scope
from app.platform.options import AuditingOptions, LoggingOptions, options


def route_of(scope: Scope) -> str | None:
    endpoint, app = scope.get("endpoint"), scope.get("app")
    for route in getattr(getattr(app, "router", None), "routes", []):
        if getattr(route, "endpoint", None) is endpoint:
            return getattr(route, "path", None)
    return None


def client_ip(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            return value.decode("latin-1").split(",")[0].strip() or None
    client = scope.get("client")
    return client[0] if client else None


def _header(scope: Scope, name: bytes) -> str | None:
    return next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == name), None)


def _parameters(content_type: str, body: bytes) -> Any:
    if not body:
        return None
    if "json" in content_type:
        try:
            return json.loads(body)
        except ValueError:
            return {"content_type": content_type, "bytes": len(body), "text": body.decode("utf-8", "replace")}
    return {"content_type": content_type.split(";")[0], "bytes": len(body)}  # an upload: its kind and size


class AuditingMiddleware:
    """`logger`: the module's request logger (e.g. "<module>.request"); default from LoggingOptions."""

    def __init__(self, app: ASGIApp, logger: str | None = None) -> None:
        self.app = app
        self.logger = logging.getLogger(logger or options(LoggingOptions).request_logger)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cid = correlation.from_headers(scope.get("headers", []))
        method = scope.get("method")
        body = bytearray()

        async def receive_teed() -> Message:
            message = await receive()
            if message["type"] == "http.request" and method not in ("GET", "HEAD"):
                body.extend(message.get("body", b""))
            return message

        async with begin_scope("request", correlation_id=cid, request_scope=scope) as audit:
            info = audit.log
            info.http_method, info.url, info.http_status = method, scope.get("path"), 500
            info.client_ip, info.browser_info = client_ip(scope), _header(scope, b"user-agent")

            async def send_with_id(message: Message) -> None:
                if message["type"] == "http.response.start":
                    info.http_status = message["status"]
                    message["headers"] = [*message.get("headers", []), *correlation.response_headers(cid)]
                await send(message)

            try:
                # A body read after routing is the actions' parameters: give them it as soon as it is known.
                await self.app(scope, receive_teed, send_with_id)
            except Exception as e:
                audit.error_type = type(e).__name__
                raise
            finally:
                info.route = route_of(scope)
                if (query := scope.get("query_string")) and _matches(info.route, options(AuditingOptions).always_log_routes):
                    info.extra["query"] = query.decode("latin-1")  # bulk data handed out: what was taken, whole
                endpoint = scope.get("endpoint")
                info.forced = getattr(endpoint, "__auditing__", None)
                params = _parameters(_header(scope, b"content-type") or "", bytes(body))
                for a in info.actions:
                    if a.parameters is None:
                        a.parameters = params
                line = {"event": "request", "request_id": cid, "method": method, "route": info.route or scope.get("path"),
                        "status": info.http_status, "duration_ms": None, "user_id": info.user_id, "user_name": info.user_name,
                        "role": info.role, "client_ip": info.client_ip,
                        "error": info.error_code or getattr(audit, "error_type", None)}
                audit.stdout = (self.logger, line)  # written when the scope ends (its duration known)


def write_request_line(audit: Any) -> None:
    """The stdout line, once the scope's duration is known (a contributor calls it)."""
    out = getattr(audit, "stdout", None)
    if out:
        lg, line = out
        line["duration_ms"] = round(audit.duration_exact or 0.0, 1)
        lg.info(json.dumps(line, separators=(",", ":")))


class RequestLineContributor:
    """Writes the request's stdout line when its scope ends (built in: registered by the auditing package)."""

    def pre_contribute(self, scope: Any) -> None:  # noqa: ARG002
        pass

    def post_contribute(self, scope: Any) -> None:
        write_request_line(scope)
