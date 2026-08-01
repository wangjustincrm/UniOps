"""API tests for the NC purchase sync admin endpoints (Task 5).

Uses this repo's real fixtures — `client` (unauthenticated), `admin_client`
(pre-authenticated as system_admin), `requester_client` (pre-authenticated as
a non-admin role) — see tests/conftest.py. The finance-api template this
mirrors (finance-api/app/api/v1/nc_sync.py) uses standalone admin_token /
clerk_token fixtures that don't exist here; this repo bakes the Authorization
header into the client fixture instead.
"""
import pytest

from app.services.nc_purchase_sync import service as svc


@pytest.mark.asyncio
async def test_status_requires_system_admin(requester_client):
    r = await requester_client.get("/api/v1/admin/nc-purchase-sync/status")
    assert r.status_code == 200
    assert r.json()["can_sync"] is False


@pytest.mark.asyncio
async def test_status_unauthenticated_rejected(client):
    r = await client.get("/api/v1/admin/nc-purchase-sync/status")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_trigger_403_for_non_admin(requester_client):
    r = await requester_client.post("/api/v1/admin/nc-purchase-sync", json={"mode": "incremental"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_trigger_full_requires_confirm(admin_client, monkeypatch):
    monkeypatch.setattr("app.services.nc_purchase_sync.service.nc_configured", lambda: True)
    r = await admin_client.post("/api/v1/admin/nc-purchase-sync", json={"mode": "full"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_trigger_503_when_not_configured(admin_client, monkeypatch):
    monkeypatch.setattr("app.services.nc_purchase_sync.service.nc_configured", lambda: False)
    r = await admin_client.post("/api/v1/admin/nc-purchase-sync", json={"mode": "incremental"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_trigger_409_when_already_running(admin_client, monkeypatch):
    monkeypatch.setattr("app.services.nc_purchase_sync.service.nc_configured", lambda: True)

    def _boom(*args, **kwargs):
        raise svc.SyncAlreadyRunning("busy")

    monkeypatch.setattr("app.services.nc_purchase_sync.service.start_run", _boom)
    r = await admin_client.post("/api/v1/admin/nc-purchase-sync", json={"mode": "incremental"})
    assert r.status_code == 409


def test_nc_purchase_sync_registered_before_admin_catchall():
    """Guards the fix for a real bug: admin_router's data-maintenance catch-alls
    (`GET /admin/{entity}`, `GET /admin/{entity}/{record_id}`) silently swallowed
    `/admin/nc-purchase-sync/*` when that router was registered after admin_router
    in app/api/v1/__init__.py — FastAPI matches routes in registration order, so
    the generic `{entity}`/`{record_id}` path params matched first. If a future
    edit reorders the include_router(...) calls, this must fail."""
    from app.api.v1 import api_router

    nc_idx = min(
        i for i, r in enumerate(api_router.routes)
        if "nc-purchase-sync" in getattr(r, "path", "")
    )
    catchall_idx = min(
        i for i, r in enumerate(api_router.routes)
        if getattr(r, "path", "") == "/admin/{entity}"
    )
    assert nc_idx < catchall_idx, (
        "nc-purchase-sync router must be registered before admin_router's "
        "/admin/{entity} catch-all in app/api/v1/__init__.py, or "
        "/admin/nc-purchase-sync/* requests will be swallowed by the "
        "data-maintenance routes instead of reaching this router"
    )
