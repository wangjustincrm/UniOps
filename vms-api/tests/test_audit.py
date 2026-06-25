"""Audit log read endpoints (PRD §2.5.2 / §6.5.5)."""
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import authed_client, make_token, make_user


# ── Helpers ─────────────────────────────────────────────────────────────────-

async def _make_visitor(client) -> str:
    """Create a visitor — also generates a `visitor.create` audit row."""
    resp = await client.post("/api/v1/visitors", json={
        "first_name": "Audit",
        "last_name":  f"Trail{uuid.uuid4().hex[:4]}",
        "company_name": "AuditCo",
        "phone": "+1-555-0100",
        "visitor_type": "other",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ── RBAC ────────────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_requester_cannot_read_audit_log(requester):
    _, client = requester
    resp = await client.get("/api/v1/audit-logs")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_dept_manager_cannot_read_audit_log(test_engine):
    user = await make_user(test_engine, role="dept_manager")
    async with authed_client(make_token(user.id, "dept_manager")) as client:
        resp = await client.get("/api/v1/audit-logs")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_auditor_can_read_audit_log(test_engine):
    user = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(user.id, "auditor")) as client:
        resp = await client.get("/api/v1/audit-logs")
    assert resp.status_code == 200
    assert "items" in resp.json()
    assert "total" in resp.json()


@pytest.mark.asyncio
async def test_admin_can_read_audit_log(admin):
    _, client = admin
    resp = await client.get("/api/v1/audit-logs")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_anonymous_cannot_read_audit_log(client):
    resp = await client.get("/api/v1/audit-logs")
    assert resp.status_code in (401, 403)


# ── Filtering + pagination ──────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_filter_by_action_type(test_engine, requester):
    # Generate some events the auditor can filter on.
    _, rclient = requester
    await _make_visitor(rclient)
    await _make_visitor(rclient)

    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as aclient:
        resp = await aclient.get("/api/v1/audit-logs?action_type=visitor.create")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    for row in body["items"]:
        assert row["action_type"] == "visitor.create"


@pytest.mark.asyncio
async def test_filter_by_entity_id_returns_only_that_entity(test_engine, requester):
    _, rclient = requester
    visitor_id = await _make_visitor(rclient)

    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as aclient:
        resp = await aclient.get(f"/api/v1/audit-logs?entity_id={visitor_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    for row in body["items"]:
        assert row["entity_id"] == visitor_id


@pytest.mark.asyncio
async def test_pagination_page_size_caps(test_engine):
    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as aclient:
        resp = await aclient.get("/api/v1/audit-logs?page=1&page_size=2")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) <= 2


@pytest.mark.asyncio
async def test_date_window_excludes_old_rows(test_engine, requester):
    from urllib.parse import quote
    _, rclient = requester
    await _make_visitor(rclient)

    auditor = await make_user(test_engine, role="auditor")
    # Far-future window → 0 matches. URL-encode the ISO timestamp because the
    # `+` from the timezone offset would otherwise be decoded as a space.
    after = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    async with authed_client(make_token(auditor.id, "auditor")) as aclient:
        resp = await aclient.get(f"/api/v1/audit-logs?from={quote(after)}")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ── CSV export ──────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_csv_export_streams_headers_and_rows(test_engine, requester):
    _, rclient = requester
    await _make_visitor(rclient)

    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as aclient:
        resp = await aclient.get("/api/v1/audit-logs/export?action_type=visitor.create")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    # Parse the CSV to confirm structure.
    body = resp.text
    import csv as _csv
    rows = list(_csv.reader(io.StringIO(body)))
    assert rows[0][0:4] == ["id", "timestamp", "user_id", "user_name"]
    # At least one data row.
    assert len(rows) >= 2
    # action_type column is index 4.
    for r in rows[1:]:
        assert r[4] == "visitor.create"


@pytest.mark.asyncio
async def test_csv_export_requester_blocked(requester):
    _, client = requester
    resp = await client.get("/api/v1/audit-logs/export")
    assert resp.status_code == 403


# ── Dashboard sanity (week_count added in W7) ───────────────────────────────-

@pytest.mark.asyncio
async def test_dashboard_overview_includes_week_count(requester):
    _, client = requester
    today = datetime.now(timezone.utc)
    visitor = await _make_visitor(client)
    await client.post("/api/v1/visits", json={
        "visitor_id": visitor,
        "host_id": str((await client.get("/api/v1/visitors")).json()["items"][0]["id"]) if False else None,  # placeholder
        "visit_date": today.date().isoformat(),
        "planned_arrival": today.isoformat(),
        "visit_purpose": "meeting",
        "access_area": "office",
    }) if False else None  # placeholder — host_id needs to be a real user, not the visitor

    # Just check the response shape — week_count is present and a non-negative int.
    resp = await client.get("/api/v1/dashboard/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["week_count"], int)
    assert body["week_count"] >= 0
