"""Cached server-side client for the identity authz hub.

Single choke point: endpoints proxy through here, and the two internal
consumers (deps.require_permission, access_scope) read get_matrix(). On
identity outage reads fall back to the frozen company_config JSONB so EPMS
stays up (writes fail with 502 — no local fallback, no drift).

get_matrix(db, token) accepts the caller's own Bearer token so identity can
authenticate the request (GET /authz/matrix requires any authenticated user).
No service account is introduced — YAGNI, Phase 2 can unify later.
"""
import time

import httpx

from app.core.config import settings

_TTL = 60.0
_cache: tuple[float, dict] | None = None


def invalidate_cache() -> None:
    """Expire the in-process matrix cache (call after successful PATCH)."""
    global _cache
    _cache = None


async def _fetch_matrix(token: str | None) -> dict:
    """Fetch the live matrix from identity using the caller's Bearer token.

    Raises httpx.HTTPStatusError (or httpx.RequestError) on failure so
    get_matrix() can fall back to the frozen JSONB.
    """
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(
            f"{settings.IDENTITY_API_URL}/identity/v1/authz/matrix",
            headers=headers,
        )
        r.raise_for_status()
        return r.json()


async def get_matrix(db, token: str | None) -> dict:
    """Effective matrix from identity (60 s process cache); fallback = frozen JSONB.

    Call chain decision:
    - deps.require_permission: has the request token via BearerToken dep → passes it.
    - access_scope._effective_permissions: no HTTP token in scope (pure DB function) →
      passes None → goes straight to fallback. Behaviour is identical to pre-Task-3
      (frozen JSONB) because identity cannot be called without a bearer token there.
    """
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < _TTL:
        return _cache[1]
    try:
        m = await _fetch_matrix(token)
        _cache = (now, m)
        return m
    except Exception:
        from app.crud import config as config_crud
        cfg = await config_crud.get_or_create(db)
        return config_crud.get_effective_role_permissions(cfg)


async def forward(method: str, path: str, token: str, json=None) -> tuple[int, dict]:
    """Pass a caller request through to identity using the caller's own Bearer token.

    Raises on connection failure (e.g. RuntimeError / httpx.ConnectError);
    callers should catch and return 502.
    """
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.request(
            method,
            f"{settings.IDENTITY_API_URL}/identity/v1{path}",
            headers=headers,
            json=json,
        )
        body = r.json() if r.content else {}
        return r.status_code, body
