"""Check-out + batch-checkout + dashboard endpoints (PRD §2.4 / §6.5)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
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
        "visitor_type": "supplier",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _payload(visitor_id: str, host_id: str, *, planned_departure_in: timedelta | None = None) -> dict:
    arrival = datetime.now(timezone.utc) + timedelta(days=1)
    body = {
        "visitor_id": visitor_id,
        "host_id": host_id,
        "visit_date": arrival.date().isoformat(),
        "planned_arrival": arrival.isoformat(),
        "visit_purpose": "meeting",
        "access_area": "office",
    }
    if planned_departure_in is not None:
        body["planned_departure"] = (arrival + planned_departure_in).isoformat()
    return body


async def _create_and_check_in(client, host_id: str, **kw) -> dict:
    visitor = await _make_visitor(client)
    created = (
        await client.post("/api/v1/visits", json=_payload(visitor, host_id, **kw))
    ).json()
    printed = await client.post(
        f"/api/v1/visits/{created['id']}/print-badge",
        json={"template_used": "standard"},
    )
    assert printed.status_code == 201, printed.text
    return printed.json()


# ── Happy path ──────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_check_out_marks_departed_and_records_actual_departure(requester):
    user, client = requester
    visit = await _create_and_check_in(client, str(user.id))
    assert visit["status"] == "checked_in"
    assert visit["actual_departure"] is None

    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/check-out",
        json={"badge_returned": True, "ppe_returned": True, "notes": "All good"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "checked_out"
    assert body["actual_departure"] is not None
    assert body["badge_returned"] is True


@pytest.mark.asyncio
async def test_cannot_check_out_visit_that_was_never_checked_in(requester):
    user, client = requester
    visitor = await _make_visitor(client)
    created = (
        await client.post("/api/v1/visits", json=_payload(visitor, str(user.id)))
    ).json()
    resp = await client.post(
        f"/api/v1/visits/{created['id']}/check-out",
        json={"badge_returned": True, "ppe_returned": True},
    )
    assert resp.status_code == 409
    assert "confirmed" in resp.text.lower()


@pytest.mark.asyncio
async def test_cannot_check_out_twice(requester):
    user, client = requester
    visit = await _create_and_check_in(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/check-out",
        json={"badge_returned": True, "ppe_returned": True},
    )
    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/check-out",
        json={"badge_returned": True, "ppe_returned": True},
    )
    assert resp.status_code == 409


# ── Scope ───────────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_cannot_check_out_other_users_visit_returns_404(test_engine):
    user_a = await make_user(test_engine, role="requester")
    user_b = await make_user(test_engine, role="requester")
    async with authed_client(make_token(user_a.id, "requester")) as ca, \
               authed_client(make_token(user_b.id, "requester")) as cb:
        visit = await _create_and_check_in(ca, str(user_a.id))
        resp = await cb.post(
            f"/api/v1/visits/{visit['id']}/check-out",
            json={"badge_returned": True, "ppe_returned": True},
        )
    assert resp.status_code == 404


# ── Audit ───────────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_check_out_writes_audit_event(test_engine, requester):
    user, client = requester
    visit = await _create_and_check_in(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/check-out",
        json={"badge_returned": True, "ppe_returned": False, "notes": "left in a hurry"},
    )

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.entity_id == uuid.UUID(visit["id"]))
                .where(AuditLog.action_type == "visit.check_out")
                .order_by(AuditLog.id.desc()).limit(1)
            )
        ).scalar_one()
    assert row.user_id == user.id
    assert (row.new_value or {}).get("status") == "checked_out"
    assert row.notes == "left in a hurry"


# ── Batch checkout (Admin) ──────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_admin_batch_checkout_closes_all_on_site(test_engine, admin):
    admin_user, admin_client = admin
    # Seed two visits checked-in by the admin (they can scope-see everything).
    # Other tests in the session may have left a checked_in visit lying around,
    # so we assert "at least mine were closed" rather than "exactly N".
    v1 = await _create_and_check_in(admin_client, str(admin_user.id))
    v2 = await _create_and_check_in(admin_client, str(admin_user.id))

    resp = await admin_client.post("/api/v1/visits/batch-checkout")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["closed"] >= 2
    assert {v1["id"], v2["id"]}.issubset(set(body["visit_ids"]))

    # Both visits are now `checked_out`.
    for vid in (v1["id"], v2["id"]):
        got = (await admin_client.get(f"/api/v1/visits/{vid}")).json()
        assert got["status"] == "checked_out"


@pytest.mark.asyncio
async def test_batch_checkout_idempotent_when_no_one_on_site(admin):
    _, client = admin
    resp = await client.post("/api/v1/visits/batch-checkout")
    assert resp.status_code == 200
    assert resp.json()["closed"] == 0


@pytest.mark.asyncio
async def test_requester_cannot_call_batch_checkout(requester):
    _, client = requester
    resp = await client.post("/api/v1/visits/batch-checkout")
    assert resp.status_code == 403


# ── Dashboard ───────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_dashboard_overview_returns_counts(requester):
    user, client = requester

    # Create one visit checked in TODAY so dashboard sees a positive number.
    visitor = await _make_visitor(client)
    today = datetime.now(timezone.utc)
    created = (
        await client.post("/api/v1/visits", json={
            "visitor_id": visitor,
            "host_id": str(user.id),
            "visit_date": today.date().isoformat(),
            "planned_arrival": today.isoformat(),
            "visit_purpose": "meeting",
            "access_area": "office",
        })
    ).json()
    await client.post(
        f"/api/v1/visits/{created['id']}/print-badge",
        json={"template_used": "standard"},
    )

    resp = await client.get("/api/v1/dashboard/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Dashboard counts are unscoped — they cover the whole plant.
    assert body["on_site_count"] >= 1
    assert body["today_count"]   >= 1
    assert body["overdue_count"] >= 0
    assert "server_time" in body


@pytest.mark.asyncio
async def test_dashboard_overdue_picks_up_planned_departure_in_past(test_engine, requester):
    user, client = requester

    # Manually craft a visit with a planned_departure that's already passed.
    # Trick: create with today's date but a planned_arrival/departure deep in
    # the past, check it in, then check the overdue counter.
    visitor = await _make_visitor(client)
    past_dep = datetime.now(timezone.utc) - timedelta(hours=2)
    past_arr = past_dep - timedelta(hours=4)
    created = (await client.post("/api/v1/visits", json={
        "visitor_id": visitor,
        "host_id": str(user.id),
        "visit_date": past_arr.date().isoformat(),
        "planned_arrival": past_arr.isoformat(),
        "planned_departure": past_dep.isoformat(),
        "visit_purpose": "meeting",
        "access_area": "office",
    })).json()
    await client.post(
        f"/api/v1/visits/{created['id']}/print-badge",
        json={"template_used": "standard"},
    )

    resp = await client.get("/api/v1/dashboard/overview")
    assert resp.status_code == 200
    assert resp.json()["overdue_count"] >= 1
