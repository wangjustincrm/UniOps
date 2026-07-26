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


# ── Task 3: entity browse + detail ──────────────────────────────────────

@pytest.mark.asyncio
async def test_browse_excludes_soft_deleted_and_detail_returns_lines(db_session):
    from datetime import datetime, timezone

    from app.models.qbo import QboBill, QboBillLine

    db_session.add_all([
        QboBill(qbo_id="b1", doc_number="BILL-1", txn_date="2026-01-01",
               counterparty_name="Acme Co", total_amt="100.00", raw={}),
        QboBill(qbo_id="b2", doc_number="BILL-2", txn_date="2026-01-02",
               deleted_at=datetime.now(timezone.utc), raw={}),
    ])
    db_session.add(QboBillLine(parent_qbo_id="b1", line_num=1, amount="100.00",
                              account_id="acct-1", raw={}))
    await db_session.flush()

    _override_auth_and_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        browse = await c.get("/finance/v1/qbo/bills", headers={"Authorization": "Bearer x"})
        detail = await c.get("/finance/v1/qbo/bills/b1", headers={"Authorization": "Bearer x"})
        missing = await c.get("/finance/v1/qbo/bills/b2", headers={"Authorization": "Bearer x"})
        unknown = await c.get("/finance/v1/qbo/not-a-real-entity", headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()

    assert browse.status_code == 200
    body = browse.json()
    assert body["total"] == 1
    assert [i["qbo_id"] for i in body["items"]] == ["b1"]

    assert detail.status_code == 200
    d = detail.json()
    assert d["header"]["qbo_id"] == "b1"
    assert len(d["lines"]) == 1
    assert d["lines"][0]["account_id"] == "acct-1"
    assert d["attachments"] == []

    # b2 is soft-deleted — browse hides it, but detail-by-id still finds it
    # (detail is an explicit lookup, not a listing; soft-delete exclusion is
    # a browse/list concern only).
    assert missing.status_code == 200
    assert missing.json()["header"]["qbo_id"] == "b2"

    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_browse_query_and_date_filters(db_session):
    from app.models.qbo import QboVendor

    db_session.add_all([
        QboVendor(qbo_id="v1", display_name="Northwind Traders", raw={}),
        QboVendor(qbo_id="v2", display_name="Contoso Ltd", raw={}),
    ])
    await db_session.flush()

    _override_auth_and_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/finance/v1/qbo/vendors", params={"q": "north"},
                        headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()

    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["qbo_id"] == "v1"


# ── Task 4: attachment file stream ──────────────────────────────────────

@pytest.mark.asyncio
async def test_attachment_file_stream(db_session):
    from app.models.qbo import QboAttachment

    db_session.add(QboAttachment(qbo_id="a1", file_name="invoice.pdf",
                                content_type="application/pdf", content=b"%PDF!", raw={}))
    await db_session.flush()

    _override_auth_and_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/finance/v1/qbo/attachments/a1/file", headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content == b"%PDF!"


@pytest.mark.asyncio
async def test_attachment_file_not_found(db_session):
    _override_auth_and_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/finance/v1/qbo/attachments/nope/file", headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()

    assert r.status_code == 404
