"""Cached server-side client for the identity authz hub.

Single choke point: endpoints proxy through here, and the two internal
consumers (deps.require_permission, access_scope) read get_matrix(). On
identity outage reads fall back to the company_config JSONB so EPMS stays up.

The JSONB is a write-through mirror, not a frozen snapshot: config.py's
PATCH /config/role-permissions writes to identity first (source of truth,
does the real validation) and then, on success, writes the same changes into
the local company_config.role_permissions row (see
config_crud.update_role_permissions). epms is the single write choke point
for the matrix, so this is race-free. The mirror exists to serve the
token=None call path (access_scope, which has no HTTP token in its pure-DB
call chain) and the outage-fallback path with a matrix that actually reflects
the latest edits, instead of one frozen at whatever it was when identity was
introduced. A mirror-write failure is logged, never surfaced to the PATCH
caller — the mirror is a cache, not the truth.

get_matrix(db, token) accepts the caller's own Bearer token so identity can
authenticate the request (GET /authz/matrix requires any authenticated user).
No service account is introduced — YAGNI, Phase 2 can unify later.

Cache sharing and token=None behaviour (Phase 1):
  - The 60 s process cache is shared across ALL callers, including token=None
    internal consumers (access_scope._effective_permissions).
  - When the cache is WARM: all callers read the cached identity matrix, which
    is at least as fresh as the mirror JSONB — matrix edits made via PATCH will
    eventually reach access_scope logic within one TTL window.  This is
    intentional.
  - When the cache is COLD and token=None (access_scope path): identity is
    NOT called at all — a tokenless request can only 403, so we go straight
    to the mirror-JSONB fallback, which the PATCH write-through keeps fresh.
    This is the fix for the cold-cache 500.
  - When the cache is COLD and token is present: httpx.HTTPStatusError
    (4xx/5xx from identity) is re-raised immediately so callers see the real
    HTTP error; network/timeout failures (httpx.RequestError and other
    non-HTTP exceptions) fall back to the mirror JSONB.
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
    get_matrix() can fall back to the mirror JSONB.
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


async def _frozen_fallback(db) -> dict:
    """Return the mirror JSONB matrix from company_config (no identity call).

    Named `_frozen_fallback` for historical reasons (Phase 1 introduced it as a
    frozen snapshot); it is now a write-through mirror kept fresh by the PATCH
    proxy in config.py — see the module docstring.
    """
    from app.crud import config as config_crud
    cfg = await config_crud.get_or_create(db)
    return config_crud.get_effective_role_permissions(cfg)


async def get_matrix(db, token: str | None) -> dict:
    """Effective matrix from identity (60 s process cache); fallback = mirror JSONB.

    Call chain decision:
    - deps.require_permission: has the request token via BearerToken dep → passes it.
    - access_scope._effective_permissions: no HTTP token in scope (pure DB function) →
      passes None.  When the cache is COLD, identity is NOT called (a tokenless
      request can only 403); we go straight to the mirror-JSONB fallback.  When the
      cache is WARM the shared cache is returned — this is intentional so access_scope
      benefits from a recently warmed matrix without making an authenticated call.

    Error handling (token present only):
    - httpx.HTTPStatusError (4xx/5xx from identity): re-raised immediately.  Callers
      that want a fallback (e.g. config.py GET endpoint) must catch this themselves.
    - httpx.RequestError / other network failures: fall back to mirror JSONB so EPMS
      stays up during transient outages.

    Cache note: the 60 s cache is shared across all callers including token=None
    access_scope consumers — see module docstring for the full behaviour.
    """
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < _TTL:
        return _cache[1]
    if token is None:
        # Deliberate no-credential call (access_scope path): never hit identity —
        # a tokenless request can only 403.  Cold cache → mirror JSONB.
        return await _frozen_fallback(db)
    try:
        m = await _fetch_matrix(token)
        _cache = (now, m)
        return m
    except httpx.HTTPStatusError:
        raise
    except Exception:
        return await _frozen_fallback(db)


async def forward(method: str, path: str, token: str | None, json=None) -> tuple[int, dict]:
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
