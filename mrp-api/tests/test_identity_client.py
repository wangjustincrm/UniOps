"""app.services.identity_client — current-user display name resolution
(mrp06 follow-up: the change-history popover must show the editor's NAME,
not their UUID).

`resolve_current_user_name` is the write-endpoint-safe wrapper
`app/api/v1/series.py`'s `PUT /series/cells` goes through. These tests
exercise it directly, at the unit level — same shape as
tests/test_mdm_client.py's coverage of `resolve_material_names` — and
specifically monkeypatch the lower-level `fetch_current_user_name` (not the
resolver itself), so the resolver's OWN try/except is what's actually under
test: every other test of this feature (tests/test_demand_series.py)
monkeypatches `resolve_current_user_name` away entirely, which is correct
for those endpoint-level tests but leaves this module's own degrade-on-
failure logic with zero direct coverage.
"""
import httpx
import pytest

from app.services import identity_client


def test_resolve_current_user_name_returns_the_name_on_success(monkeypatch):
    monkeypatch.setattr(
        identity_client, "fetch_current_user_name", lambda token: "Jane Planner",
    )
    assert identity_client.resolve_current_user_name("some-token") == "Jane Planner"


def test_resolve_current_user_name_degrades_to_none_when_identity_unreachable(monkeypatch):
    """identity-api being down (connection refused, timeout, ...) must never
    raise out of resolve_current_user_name — a name lookup must never break
    a save."""
    def _boom(token):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(identity_client, "fetch_current_user_name", _boom)
    assert identity_client.resolve_current_user_name("some-token") is None


def test_resolve_current_user_name_degrades_on_non_2xx_status(monkeypatch):
    """A reachable-but-erroring identity-api (401/500/...) must degrade
    exactly like an unreachable one — fetch_current_user_name raises
    httpx.HTTPStatusError via resp.raise_for_status(), which must also be
    swallowed."""
    def _boom(token):
        request = httpx.Request("GET", "http://identity/identity/v1/auth/me")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr(identity_client, "fetch_current_user_name", _boom)
    assert identity_client.resolve_current_user_name("some-token") is None


def test_resolve_current_user_name_degrades_on_any_other_exception(monkeypatch):
    """The except clause is a bare `except Exception`, not just httpx.HTTPError
    -- pin that a non-httpx failure (e.g. a malformed body breaking
    resp.json()) degrades the same way rather than propagating."""
    def _boom(token):
        raise ValueError("malformed response body")

    monkeypatch.setattr(identity_client, "fetch_current_user_name", _boom)
    assert identity_client.resolve_current_user_name("some-token") is None
