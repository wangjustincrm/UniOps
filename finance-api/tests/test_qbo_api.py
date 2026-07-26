"""QBO mirror API — sync trigger/status/runs (Task 2), entity browse/detail
(Task 3), attachment file stream (Task 4). Auth is CurrentUser-only (no
permission gate), so tests override get_token_payload directly rather than
minting a real JWT + seeding role_permissions."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_token_payload
from app.db.base import get_db
from app.main import app
from app.services import qbo_sync


def _override_auth_and_db(db_session):
    app.dependency_overrides[get_token_payload] = lambda: {
        "sub": "00000000-0000-0000-0000-000000000001", "role": "system_admin"}

    async def _db():
        yield db_session
    app.dependency_overrides[get_db] = _db


@pytest.mark.asyncio
async def test_status_shape(db_session, monkeypatch):
    monkeypatch.setattr(qbo_sync, "qbo_configured", lambda: True)
    _override_auth_and_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/finance/v1/qbo/sync/status", headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"can_sync", "configured", "current_run", "last_run"}
    assert body["configured"] is True
    assert body["current_run"] is None
    assert body["last_run"] is None


@pytest.mark.asyncio
async def test_sync_runs_empty(db_session, monkeypatch):
    monkeypatch.setattr(qbo_sync, "qbo_configured", lambda: True)
    _override_auth_and_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/finance/v1/qbo/sync/runs", headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json() == {"items": []}


@pytest.mark.asyncio
async def test_sync_not_configured(db_session, monkeypatch):
    monkeypatch.setattr(qbo_sync, "qbo_configured", lambda: False)
    _override_auth_and_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/finance/v1/qbo/sync", json={"mode": "incremental"},
                         headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_full_sync_requires_confirm(db_session, monkeypatch):
    monkeypatch.setattr(qbo_sync, "qbo_configured", lambda: True)
    called = {}
    monkeypatch.setattr(qbo_sync, "launch_sync", lambda **kw: called.update(kw))
    _override_auth_and_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        bad = await c.post("/finance/v1/qbo/sync", json={"mode": "full"},
                           headers={"Authorization": "Bearer x"})
        good = await c.post("/finance/v1/qbo/sync", json={"mode": "full", "confirm": "RELOAD"},
                            headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()
    assert bad.status_code == 422
    assert good.status_code == 202
    assert good.json() == {"status": "started"}
    assert called.get("mode") == "full"


@pytest.mark.asyncio
async def test_incremental_sync_no_confirm_needed(db_session, monkeypatch):
    monkeypatch.setattr(qbo_sync, "qbo_configured", lambda: True)
    called = {}
    monkeypatch.setattr(qbo_sync, "launch_sync", lambda **kw: called.update(kw))
    _override_auth_and_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/finance/v1/qbo/sync", json={"mode": "incremental"},
                         headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()
    assert r.status_code == 202
    assert called.get("mode") == "incremental"


@pytest.mark.asyncio
async def test_sync_rejected_when_already_running(db_session, monkeypatch):
    import uuid
    from datetime import datetime, timezone

    from app.models.qbo import QboSyncRun

    monkeypatch.setattr(qbo_sync, "qbo_configured", lambda: True)
    monkeypatch.setattr(qbo_sync, "launch_sync", lambda **kw: None)
    db_session.add(QboSyncRun(id=uuid.uuid4(), mode="incremental", status="running",
                              started_at=datetime.now(timezone.utc)))
    await db_session.flush()
    _override_auth_and_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/finance/v1/qbo/sync", json={"mode": "incremental"},
                         headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()
    assert r.status_code == 409
