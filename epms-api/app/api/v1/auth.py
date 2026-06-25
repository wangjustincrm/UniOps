"""Auth — thin proxy to identity-api (Phase 0-B4).

The implementation moved to identity-api (/identity/v1/auth/*); this catch-all
preserves the public contract (/api/v1/auth/*) so all four frontends keep
working unchanged. Tokens are identical (same secret/claims). Frontends will
switch to identity directly during Phase a, after which this proxy is removed.

Tests moved with the implementation: identity-api/tests/test_auth_flow.py.
"""
import logging

import httpx
from fastapi import APIRouter, Request, Response

from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_TIMEOUT = httpx.Timeout(15.0, connect=3.0)
_HOP_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}


@router.api_route("/{path:path}", methods=["GET", "POST", "PATCH"])
async def proxy_auth(path: str, request: Request) -> Response:
    url = f"{settings.IDENTITY_API_URL}/identity/v1/auth/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS}
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(request.method, url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        logger.error("identity-api unreachable at %s: %s", settings.IDENTITY_API_URL, exc)
        return Response(
            content=b'{"detail":"Identity service unavailable"}',
            status_code=502, media_type="application/json",
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type"),
    )
