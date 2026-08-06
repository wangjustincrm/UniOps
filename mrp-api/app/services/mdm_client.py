"""Thin client mrp-api uses to resolve material codes -> names via mdm-api.

Follows the established cross-service pattern (see
epms-api/app/services/mdm_client.py): plain HTTP to mdm-api, forwarding the
caller's bearer token, never reading mdm's tables directly. This module
generalizes the paging loop `app/services/forecast_io.py`'s
`fetch_valid_material_codes` already used to page through every material
once for import validation, so a *second* piece of code (name lookups for
read endpoints) doesn't reinvent a third way to call mdm-api —
`forecast_io.fetch_valid_material_codes` now derives from `fetch_materials`
below rather than duplicating its own httpx.Client/paging logic.

Two entry points, for two different callers with different failure
tolerances:

- `fetch_materials(token)` — synchronous, blocking `httpx.Client`, pages
  through `GET /mdm/v1/materials` (page_size=500) until exhausted. Kept
  *synchronous* (not `async def`) for the same reason
  `forecast_io.fetch_valid_material_codes` was: tests monkeypatch this exact
  module attribute with a plain callable (no async mock needed), and callers
  bridge it onto a worker thread via `anyio.to_thread.run_sync` themselves.
  Raises (`httpx.HTTPError`) on any failure — used by `import_forecast`,
  which must turn an mdm-api outage into a loud, clear 503 rather than
  silently importing against an empty/wrong code set.

- `resolve_material_names(token)` — the read-endpoint-safe wrapper around
  `fetch_materials`. Runs it off the event loop (same `anyio.to_thread`
  idiom `app/api/v1/consignment.py`'s lot-lookup endpoints and
  `import_forecast` already use for their own blocking calls) and NEVER
  raises: the forecast grid / consignment stock list / net-requirement
  breakdown are all primary read screens whose actual numbers never come
  from mdm-api, so mdm-api being slow, unreachable, or erroring must
  degrade those rows to `name: None` (today's behavior, before this
  module existed) instead of 5xx-ing or hanging a read.

  The try/except lives *inside* `_fetch_materials_safe`, the function that
  actually runs on the worker thread — not wrapped around the
  `await anyio.to_thread.run_sync(...)` call itself. This was verified the
  hard way: wrapping the try/except around the await instead (so the
  exception crosses the anyio/greenlet thread boundary before being caught)
  reproduced a real, deterministic bug against this service's async
  SQLAlchemy session — a subsequent `SELECT` on the *same* request/session
  would come back with stale (pre-commit, un-refreshed) `Numeric` column
  values instead of the DB's actual stored precision, breaking
  test_export_then_reimport_round_trips_clean (mrp-api/tests/test_forecast_io.py)
  in a way that had nothing to do with material names. Letting an exception
  propagate out of a worker thread back across `anyio.to_thread.run_sync`
  while the same task holds an open `AsyncSession` is the trigger — catching
  it before it ever leaves the thread sidesteps that entirely. Any failure
  (network error, non-2xx, whatever) is logged as a warning and swallowed on
  the worker thread, returning `{}` so every subsequent `.get(code)` lookup
  on the caller side comes back `None` uniformly — never raises across the
  thread boundary.
"""
from __future__ import annotations

import logging

import anyio
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def fetch_materials(token: str) -> dict[str, str | None]:
    """Pull the full material_code -> name map known to mdm-api, once.

    Pages through GET /mdm/v1/materials (page_size=500) until exhausted.
    Forwards the caller's bearer token so this respects mdm-api's own authz
    (materials reads there are open to any authenticated role). Raises
    `httpx.HTTPError` (network failure or non-2xx) — callers that must
    degrade instead of fail should go through `resolve_material_names`.
    """
    materials: dict[str, str | None] = {}
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(
        base_url=f"{settings.MDM_API_URL}/mdm/v1",
        timeout=settings.MDM_API_TIMEOUT_SECONDS,
        headers=headers,
    ) as http:
        page = 1
        page_size = 500
        while True:
            resp = http.get("/materials", params={"page": page, "page_size": page_size})
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            for item in items:
                code = item.get("code")
                if code:
                    materials[code] = item.get("name")
            total = data.get("total", len(items))
            if not items or page * page_size >= total:
                break
            page += 1
    return materials


def _fetch_materials_safe(token: str) -> dict[str, str | None]:
    """`fetch_materials`, but never raises — runs entirely on the worker
    thread `resolve_material_names` dispatches to. See this module's
    docstring for why the try/except must live here and not around the
    `await anyio.to_thread.run_sync(...)` call in `resolve_material_names`.
    """
    try:
        return fetch_materials(token)
    except Exception:
        logger.warning(
            "mdm-api material name lookup failed — degrading to name=null",
            exc_info=True,
        )
        return {}


async def resolve_material_names(token: str) -> dict[str, str | None]:
    """Read-endpoint-safe wrapper: fetch material names, never raise.

    Used by GET .../grid, GET .../export, GET /consignment/stock, and
    GET /net-requirement to fill in a `name` alongside each `material_code`
    they already return. mdm-api being unavailable must not break any of
    those reads — `_fetch_materials_safe` (see its docstring, and this
    module's, for why the error handling lives there and not here) never
    raises, so nothing ever crosses this await as an exception; every
    subsequent `.get(code)` on the caller side simply comes back `None`,
    i.e. the same "name always null" behavior these endpoints had before
    this lookup existed.
    """
    return await anyio.to_thread.run_sync(_fetch_materials_safe, token)
