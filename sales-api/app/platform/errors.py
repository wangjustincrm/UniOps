"""One error shape for the whole API (design §13.13).

A `BusinessError` is an expected refusal — a rule, a missing row, a bad query
key — and is answered as

    {"error": {"code": "...", "message": "...", "details": {...}}}

with its HTTP status (409 by default, 422 for invalid input, 404 for a missing
row). A request the API's schemas refuse is answered in the same shape:
code `request.invalid`, 422, `details.errors` = [{field, message}] and
`details.field` = the first field, so a form can put each message next to
its input. Anything else is a bug: it is logged with a correlation id and
answered as a 500 carrying only that id.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class BusinessError(Exception):
    """An expected refusal with a stable machine-readable `code`."""

    status_code: int = 409

    def __init__(
        self,
        code: str,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code

    def to_body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class InvalidInput(BusinessError):
    status_code = 422


class NotFound(BusinessError):
    status_code = 404

    def __init__(self, entity: str, id: Any) -> None:
        super().__init__("entity.not_found", f"{entity} {id} not found", {"entity": entity, "id": str(id)})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(BusinessError)
    async def _business(request: Request, exc: BusinessError):  # noqa: ARG001
        return JSONResponse(status_code=exc.status_code, content=exc.to_body())

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):  # noqa: ARG001
        errors = [{"field": _field(e.get("loc", ())), "message": e.get("msg", "invalid")} for e in exc.errors()]
        first = next((e["field"] for e in errors if e["field"]), None)
        message = "; ".join(f"{e['field']}: {e['message']}" if e["field"] else e["message"] for e in errors)
        return JSONResponse(status_code=422, content=InvalidInput(
            "request.invalid", message or "the request is not valid", {"field": first, "errors": errors},
        ).to_body())

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):  # noqa: ARG001
        correlation_id = uuid.uuid4().hex
        logger.exception(
            "Unhandled error %s on %s %s", correlation_id, request.method, request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={"error": {
                "code": "internal",
                "message": "Internal server error",
                "details": {"correlation_id": correlation_id},
            }},
        )


def _field(loc: tuple | list) -> str | None:
    """`("body", "invoice_email")` → "invoice_email"; the request part is dropped."""
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts) or None
