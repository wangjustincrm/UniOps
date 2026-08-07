"""Thin client mrp-api uses to resolve the CURRENT caller's display name via
identity-api, for the Continuous Sales Forecast follow-up (change-history
popover must show a NAME, not a UUID).

Follows the same style as `app/services/mdm_client.py` (see that module's
docstring for the general pattern this mirrors): plain synchronous
`httpx.Client`, forwarding the caller's bearer token, never reading
identity-api's tables directly.

Why write-time denormalization, not a read-time lookup: the access JWT
carries only `{sub, role, type}` (no name), and identity-api has NO
batch/list-users endpoint to turn a `changed_by` UUID back into a name after
the fact — only `GET /identity/v1/auth/me`, which returns the CURRENT
caller's own profile. Since the editor of a cell IS the current user at
*write* time, `app/api/v1/series.py`'s `PUT /series/cells` resolves the name
ONCE per request (not per cell) via `fetch_current_user_name` and stores it
on every `MrpForecastChangeLog` row that write produces
(`app/services/demand_series.py::upsert_cells`'s `changed_by_name` param) —
so a read of the change log never needs identity-api at all.

One entry point, mirroring `mdm_client.py`'s raises-vs-degrades split:

- `fetch_current_user_name(token)` — synchronous, blocking `httpx.Client`
  call to `GET {IDENTITY_API_URL}/identity/v1/auth/me`, returns
  `resp.json().get("full_name")`. Raises (`httpx.HTTPError`, or anything
  `resp.raise_for_status()`/`resp.json()` can throw) on any failure — this
  is the "loud" half; `resolve_current_user_name` below is the
  write-endpoint-safe wrapper around it.

- `resolve_current_user_name(token)` — the safe wrapper `PUT /series/cells`
  actually calls. NEVER raises: a name lookup must never break a save (the
  design brief's explicit requirement) — identity-api being slow,
  unreachable, or erroring degrades to `None` (no name recorded, same as a
  pre-mrp06 row), logged as a warning, exactly like `mdm_client.py`'s
  `resolve_material_names`/`resolve_shelf_life` degrade instead of raising
  for their own read-endpoint callers.

Kept synchronous (not `async def`), same reasoning as `mdm_client.py`: tests
monkeypatch this exact module attribute with a plain callable, and
`app/api/v1/series.py` imports `resolve_current_user_name` as a bare name
(not accessed via this module) so a test can
`monkeypatch.setattr(series, "resolve_current_user_name", ...)` — same idiom
`app/api/v1/consignment.py` uses for `lookup_lot`. This call is a single
request (not `mdm_client.py`'s paging loop), so it's called directly rather
than bounced onto a worker thread via `anyio.to_thread.run_sync` — a single
short GET, bounded by `IDENTITY_API_TIMEOUT_SECONDS`, blocking the event
loop briefly is judged an acceptable trade-off on this one write path,
consistent with how small identity-api gets is documented in
`app/core/config.py`.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def fetch_current_user_name(token: str) -> str | None:
    """GET {IDENTITY_API_URL}/identity/v1/auth/me with the caller's bearer
    token, return the current user's `full_name`. Raises `httpx.HTTPError`
    (network failure or non-2xx) — callers that must degrade instead of fail
    should go through `resolve_current_user_name`.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(
        base_url=settings.IDENTITY_API_URL,
        timeout=settings.IDENTITY_API_TIMEOUT_SECONDS,
        headers=headers,
    ) as http:
        resp = http.get("/identity/v1/auth/me")
        resp.raise_for_status()
        return resp.json().get("full_name")


def resolve_current_user_name(token: str) -> str | None:
    """`fetch_current_user_name`, but never raises — a name lookup must
    never break a save. Any failure (network error, non-2xx, malformed
    body, whatever) is logged as a warning and swallowed, returning `None`
    so `PUT /series/cells` (app/api/v1/series.py) simply records
    `changed_by_name=None` for the whole request's change-log rows — the
    same "history entry still gets written, just without a name" outcome a
    pre-mrp06 row already has.
    """
    try:
        return fetch_current_user_name(token)
    except Exception:
        logger.warning(
            "identity-api current-user name lookup failed — degrading to "
            "changed_by_name=null",
            exc_info=True,
        )
        return None
