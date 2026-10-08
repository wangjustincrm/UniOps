"""Visit attachment endpoints (W11+W12 S2-E / PRD §6.5.6).

vms-api proxies uploads to file-api. In tests we monkey-patch the proxy
so we don't depend on a running file-api — the goal here is to verify
vms-api's side: RBAC, visibility scope, audit log, list semantics.
"""
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as session_module
from app.models.audit_log import AuditLog
from app.services import attachments as attachments_svc
from tests.conftest import authed_client, make_token, make_user


# ── Helpers ─────────────────────────────────────────────────────────────────-

async def _make_visitor(client) -> str:
    resp = await client.post("/api/v1/visitors", json={
        "first_name": "Attach",
        "last_name":  f"Test{uuid.uuid4().hex[:4]}",
        "company_name": "AttachCo",
        "phone": "+1-555-0800",
        "visitor_type": "supplier",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _payload(visitor_id: str, host_id: str) -> dict:
    arrival = datetime.now(timezone.utc) + timedelta(days=1)
    return {
        "visitor_id": visitor_id,
        "host_id": host_id,
        "visit_date": arrival.date().isoformat(),
        "planned_arrival": arrival.isoformat(),
        "planned_departure": (arrival + timedelta(hours=2)).isoformat(),
        "visit_purpose": "meeting",
        "access_area": "office",
    }


async def _insert_file_metadata_row(
    *, visit_id: str, filename: str, uploaded_by: str,
) -> str:
    """Insert directly into the file_metadata mirror — simulates a row
    that file-api wrote in production. Returns the new file id."""
    file_id = uuid.uuid4()
    async with session_module.AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO file_metadata "
                "(id, original_filename, content_type, file_size, storage_path, "
                " uploaded_by, service, doc_type, doc_id, is_deleted, created_at) "
                "VALUES (:id, :fn, 'application/pdf', 1024, '/tmp/x', "
                " :uploader, 'vms', 'vms_visit', :doc, FALSE, NOW())"
            ),
            {"id": file_id, "fn": filename, "uploader": uuid.UUID(uploaded_by),
             "doc": uuid.UUID(visit_id)},
        )
        await db.commit()
    return str(file_id)


# ── Patch upload_attachment so we don't hit the network ─────────────────────-

@pytest.fixture
def fake_file_api(monkeypatch):
    captured: list[dict] = []

    async def _fake_upload(*, visit_id, bearer_token, filename, content_type, data):
        file_id = await _insert_file_metadata_row(
            visit_id=str(visit_id),
            filename=filename,
            uploaded_by="00000000-0000-0000-0000-000000000000",
        )
        captured.append({
            "visit_id": str(visit_id),
            "filename": filename,
            "size": len(data),
        })
        return {
            "id": file_id,
            "original_filename": filename,
            "content_type": content_type,
            "file_size": len(data),
            "service": "vms",
            "doc_type": "vms_visit",
            "doc_id": str(visit_id),
        }

    monkeypatch.setattr(attachments_svc, "upload_attachment", _fake_upload)
    return captured


# ── List ────────────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_list_attachments_empty_for_new_visit(requester):
    user, client = requester
    visitor = await _make_visitor(client)
    visit = (await client.post("/api/v1/visits", json=_payload(visitor, str(user.id)))).json()

    resp = await client.get(f"/api/v1/visits/{visit['id']}/attachments")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_attachments_includes_inserted_rows(requester):
    user, client = requester
    visitor = await _make_visitor(client)
    visit = (await client.post("/api/v1/visits", json=_payload(visitor, str(user.id)))).json()
    file_id = await _insert_file_metadata_row(
        visit_id=visit["id"], filename="passport.pdf", uploaded_by=str(user.id),
    )

    resp = await client.get(f"/api/v1/visits/{visit['id']}/attachments")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == file_id
    assert body[0]["original_filename"] == "passport.pdf"
    assert body[0]["doc_type"] == "vms_visit"
    assert "download_url" in body[0]


@pytest.mark.asyncio
async def test_list_attachments_404_for_invisible_visit(test_engine, requester):
    """Requester A sees 404 for Requester B's visit's attachments."""
    user_a, _ = requester
    user_b = await make_user(test_engine, role="requester")
    async with authed_client(make_token(user_b.id, "requester")) as cb:
        visitor = await _make_visitor(cb)
        visit_b = (await cb.post("/api/v1/visits", json=_payload(visitor, str(user_b.id)))).json()

    # user_a (the requester fixture) tries to read user_b's visit attachments.
    _, ca = requester
    resp = await ca.get(f"/api/v1/visits/{visit_b['id']}/attachments")
    assert resp.status_code == 404


# ── Upload (proxy) ──────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_upload_attachment_proxies_and_logs_audit(requester, fake_file_api):
    user, client = requester
    visitor = await _make_visitor(client)
    visit = (await client.post("/api/v1/visits", json=_payload(visitor, str(user.id)))).json()

    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/attachments",
        files={"file": ("permit.pdf", b"hello world", "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["original_filename"] == "permit.pdf"

    # Proxy was actually invoked with right params.
    assert len(fake_file_api) == 1
    assert fake_file_api[0]["filename"] == "permit.pdf"
    assert fake_file_api[0]["visit_id"] == visit["id"]
    assert fake_file_api[0]["size"] == len(b"hello world")

    # Audit log captures the upload.
    async with session_module.AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AuditLog).where(
                AuditLog.action_type == "visit.attachment.upload",
                AuditLog.entity_id == uuid.UUID(visit["id"]),
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].new_value["filename"] == "permit.pdf"


@pytest.mark.asyncio
async def test_auditor_cannot_upload_attachment(test_engine, requester, fake_file_api):
    """Auditor is read-only — cannot mutate, including attachments."""
    user_host, client_host = requester
    visitor = await _make_visitor(client_host)
    visit = (await client_host.post(
        "/api/v1/visits", json=_payload(visitor, str(user_host.id))
    )).json()

    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as ca:
        resp = await ca.post(
            f"/api/v1/visits/{visit['id']}/attachments",
            files={"file": ("audit.pdf", b"x", "application/pdf")},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_upload_attachment_returns_404_for_invisible_visit(
    test_engine, requester, fake_file_api,
):
    user_b = await make_user(test_engine, role="requester")
    async with authed_client(make_token(user_b.id, "requester")) as cb:
        visitor = await _make_visitor(cb)
        visit_b = (await cb.post(
            "/api/v1/visits", json=_payload(visitor, str(user_b.id))
        )).json()

    # user_a (requester fixture) cannot see user_b's visit.
    _, ca = requester
    resp = await ca.post(
        f"/api/v1/visits/{visit_b['id']}/attachments",
        files={"file": ("hax.pdf", b"x", "application/pdf")},
    )
    assert resp.status_code == 404


# ── Download through vms-api (D-14) ─────────────────────────────────────────-

async def test_download_goes_through_vms_api_and_respects_scope(test_engine, requester, monkeypatch):
    """The listing used to hand out file-api's internal URL, which a browser
    link cannot authenticate against. Downloads now go through vms-api."""
    user, client = requester
    vid = await _make_visitor(client)
    visit = (await client.post("/api/v1/visits", json=_payload(vid, str(user.id)))).json()
    fid = await _insert_file_metadata_row(visit_id=visit["id"], filename="permit é.pdf", uploaded_by=str(user.id))

    class _Resp:
        content = b"%PDF-1.4 fake"
        def raise_for_status(self):
            pass

    calls = []

    class _Client:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, headers=None):
            calls.append((url, headers))
            return _Resp()

    monkeypatch.setattr(attachments_svc.httpx, "AsyncClient", _Client)

    listed = (await client.get(f"/api/v1/visits/{visit['id']}/attachments")).json()
    assert listed[0]["download_url"] == f"/api/v1/visits/{visit['id']}/attachments/{fid}/download"

    r = await client.get(listed[0]["download_url"])
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 fake"
    assert r.headers["content-type"] == "application/pdf"
    assert "permit" in r.headers["content-disposition"]
    assert calls and calls[0][1]["Authorization"].startswith("Bearer ")

    # A file id from another visit is not served through this visit.
    other = (await client.post("/api/v1/visits", json=_payload(vid, str(user.id)))).json()
    assert (await client.get(f"/api/v1/visits/{other['id']}/attachments/{fid}/download")).status_code == 404

    # Someone who cannot see the visit gets 404.
    stranger = await make_user(test_engine, role="requester")
    async with authed_client(make_token(stranger.id, stranger.role)) as sc:
        assert (await sc.get(listed[0]["download_url"])).status_code == 404
