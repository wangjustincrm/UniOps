"""Visit CRUD endpoints + visibility scope + audit log (PRD §3.2 / §6.5.2 / §2.5.2)."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit_log import AuditLog
from tests.conftest import authed_client, make_token, make_user


# ── Helpers ─────────────────────────────────────────────────────────────────-

async def _make_visitor(client) -> str:
    resp = await client.post("/api/v1/visitors", json={
        "first_name": "Test",
        "last_name":  f"Visitor{uuid.uuid4().hex[:4]}",
        "company_name": "TestCo",
        "phone": "+1-555-0100",
        "email": None,
        "visitor_type": "supplier",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _visit_payload(visitor_id: str, host_id: str, access_area: str = "office") -> dict:
    arrival = datetime.now(timezone.utc) + timedelta(days=1)
    return {
        "visitor_id": visitor_id,
        "host_id": host_id,
        "visit_date": arrival.date().isoformat(),
        "planned_arrival": arrival.isoformat(),
        "planned_departure": (arrival + timedelta(hours=2)).isoformat(),
        "visit_purpose": "meeting",
        "access_area": access_area,
    }


# ── Happy-path CRUD ─────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_requester_can_create_visit(requester):
    user, client = requester
    visitor_id = await _make_visitor(client)
    payload = _visit_payload(visitor_id, str(user.id))
    resp = await client.post("/api/v1/visits", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["created_by"] == str(user.id)
    assert body["host_id"] == str(user.id)


@pytest.mark.asyncio
async def test_get_own_visit(requester):
    user, client = requester
    visitor_id = await _make_visitor(client)
    created = (await client.post(
        "/api/v1/visits", json=_visit_payload(visitor_id, str(user.id))
    )).json()
    got = await client.get(f"/api/v1/visits/{created['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == created["id"]


@pytest.mark.asyncio
async def test_list_returns_own_visits_only_for_requester(test_engine):
    """Requester A cannot see Requester B's visits."""
    user_a = await make_user(test_engine, role="requester")
    user_b = await make_user(test_engine, role="requester")
    async with authed_client(make_token(user_a.id, "requester")) as ca, \
               authed_client(make_token(user_b.id, "requester")) as cb:
        visitor_a = await _make_visitor(ca)
        visitor_b = await _make_visitor(cb)
        await ca.post("/api/v1/visits", json=_visit_payload(visitor_a, str(user_a.id)))
        await cb.post("/api/v1/visits", json=_visit_payload(visitor_b, str(user_b.id)))
        a_list = (await ca.get("/api/v1/visits")).json()
        b_list = (await cb.get("/api/v1/visits")).json()
    # A sees ONLY their visit; B sees ONLY theirs.
    assert {v["host_id"] for v in a_list["items"]} == {str(user_a.id)}
    assert {v["host_id"] for v in b_list["items"]} == {str(user_b.id)}


@pytest.mark.asyncio
async def test_get_other_users_visit_returns_404_not_403(test_engine):
    """Per visit.is_visible — leak nothing about visits outside scope."""
    user_a = await make_user(test_engine, role="requester")
    user_b = await make_user(test_engine, role="requester")
    async with authed_client(make_token(user_a.id, "requester")) as ca, \
               authed_client(make_token(user_b.id, "requester")) as cb:
        visitor = await _make_visitor(cb)
        created = (await cb.post(
            "/api/v1/visits", json=_visit_payload(visitor, str(user_b.id))
        )).json()
        resp = await ca.get(f"/api/v1/visits/{created['id']}")
    assert resp.status_code == 404


# ── Dept-manager visibility ─────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_dept_manager_sees_visits_in_their_department(test_engine):
    dept = uuid.uuid4()
    other_dept = uuid.uuid4()
    manager = await make_user(test_engine, role="dept_manager", department_id=dept)
    teammate = await make_user(test_engine, role="requester", department_id=dept)
    outsider = await make_user(test_engine, role="requester", department_id=other_dept)

    async with authed_client(make_token(teammate.id, "requester")) as c_teammate, \
               authed_client(make_token(outsider.id, "requester")) as c_outsider, \
               authed_client(make_token(manager.id, "dept_manager")) as c_mgr:
        visitor = await _make_visitor(c_teammate)
        await c_teammate.post(
            "/api/v1/visits", json=_visit_payload(visitor, str(teammate.id))
        )
        visitor2 = await _make_visitor(c_outsider)
        await c_outsider.post(
            "/api/v1/visits", json=_visit_payload(visitor2, str(outsider.id))
        )
        listed = (await c_mgr.get("/api/v1/visits")).json()

    host_ids = {v["host_id"] for v in listed["items"]}
    assert str(teammate.id) in host_ids, "Manager should see teammate's visit"
    assert str(outsider.id) not in host_ids, "Manager should NOT see outsider's visit"


@pytest.mark.asyncio
async def test_auditor_sees_everyone(test_engine):
    auditor_user = await make_user(test_engine, role="auditor")
    host_a = await make_user(test_engine, role="requester")
    host_b = await make_user(test_engine, role="requester")
    async with authed_client(make_token(host_a.id, "requester")) as ca, \
               authed_client(make_token(host_b.id, "requester")) as cb, \
               authed_client(make_token(auditor_user.id, "auditor")) as c_aud:
        v_a = await _make_visitor(ca)
        v_b = await _make_visitor(cb)
        await ca.post("/api/v1/visits", json=_visit_payload(v_a, str(host_a.id)))
        await cb.post("/api/v1/visits", json=_visit_payload(v_b, str(host_b.id)))
        listed = (await c_aud.get("/api/v1/visits")).json()
    host_ids = {v["host_id"] for v in listed["items"]}
    assert str(host_a.id) in host_ids
    assert str(host_b.id) in host_ids


# ── Cancel ──────────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_cancel_own_visit(requester):
    user, client = requester
    visitor = await _make_visitor(client)
    created = (await client.post(
        "/api/v1/visits", json=_visit_payload(visitor, str(user.id))
    )).json()
    resp = await client.post(f"/api/v1/visits/{created['id']}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cannot_cancel_other_users_visit(test_engine):
    user_a = await make_user(test_engine, role="requester")
    user_b = await make_user(test_engine, role="requester")
    async with authed_client(make_token(user_a.id, "requester")) as ca, \
               authed_client(make_token(user_b.id, "requester")) as cb:
        visitor = await _make_visitor(ca)
        created = (await ca.post(
            "/api/v1/visits", json=_visit_payload(visitor, str(user_a.id))
        )).json()
        # B cannot see it at all (404).
        resp = await cb.post(f"/api/v1/visits/{created['id']}/cancel")
    assert resp.status_code == 404


# ── /active ─────────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_active_returns_only_checked_in(test_engine, requester):
    """An untouched visit (status=confirmed) does NOT appear in /active."""
    _, client = requester
    visitor = await _make_visitor(client)
    user, _ = requester
    await client.post("/api/v1/visits", json=_visit_payload(visitor, str(user.id)))
    resp = await client.get("/api/v1/visits/active")
    assert resp.status_code == 200, resp.text
    # No badge print has happened (S1-E), so nothing should be checked_in yet.
    assert resp.json() == []


# ── Patch + status gate ─────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_patch_succeeds_in_confirmed_status(requester):
    user, client = requester
    visitor = await _make_visitor(client)
    created = (await client.post(
        "/api/v1/visits", json=_visit_payload(visitor, str(user.id))
    )).json()
    resp = await client.patch(
        f"/api/v1/visits/{created['id']}",
        json={"notes": "Will bring laptop", "vehicle_plate": "BAYM-001"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["notes"] == "Will bring laptop"
    assert resp.json()["vehicle_plate"] == "BAYM-001"


@pytest.mark.asyncio
async def test_patch_fails_after_cancel(requester):
    user, client = requester
    visitor = await _make_visitor(client)
    created = (await client.post(
        "/api/v1/visits", json=_visit_payload(visitor, str(user.id))
    )).json()
    await client.post(f"/api/v1/visits/{created['id']}/cancel")
    resp = await client.patch(
        f"/api/v1/visits/{created['id']}", json={"notes": "too late"}
    )
    assert resp.status_code == 409


# ── Audit log ───────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_mutations_write_audit_log_rows(test_engine, requester):
    """Each mutation appends exactly one row to vms_audit_logs."""
    user, client = requester

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        before = (await db.execute(select(func.count(AuditLog.id)))).scalar_one()

    visitor = await _make_visitor(client)                                         # visitor.create
    created = (await client.post(
        "/api/v1/visits", json=_visit_payload(visitor, str(user.id))
    )).json()                                                                     # visit.create
    await client.patch(f"/api/v1/visits/{created['id']}", json={"notes": "n"})    # visit.update
    await client.post(f"/api/v1/visits/{created['id']}/cancel")                    # visit.cancel

    async with factory() as db:
        after = (await db.execute(select(func.count(AuditLog.id)))).scalar_one()
        # Most recent action types in order:
        rows = (
            await db.execute(
                select(AuditLog.action_type, AuditLog.entity_type)
                .order_by(AuditLog.id.desc()).limit(4)
            )
        ).all()

    assert after - before == 4, f"expected 4 audit rows, got {after - before}"
    actions = [r[0] for r in rows]
    assert "visit.cancel" in actions
    assert "visit.update" in actions
    assert "visit.create" in actions
    assert "visitor.create" in actions


@pytest.mark.asyncio
async def test_audit_row_captures_user_and_ip(test_engine, requester):
    user, client = requester
    visitor = await _make_visitor(client)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (
            await db.execute(
                select(AuditLog).where(AuditLog.action_type == "visitor.create")
                .order_by(AuditLog.id.desc()).limit(1)
            )
        ).scalar_one()
    assert row.user_id == user.id
    assert row.user_name == user.full_name
    assert row.entity_type == "visitor"
    assert row.entity_id == uuid.UUID(visitor)
    assert row.ip_address  # populated (httpx ASGI transport supplies a host)
    assert row.new_value is not None
    assert row.new_value.get("first_name") == "Test"
