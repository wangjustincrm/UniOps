"""Badge printing = check-in (PRD §2.2 + §2.3 / VMS-LB-007 / VMS-LB-008)."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit_log import AuditLog
from app.models.badge_print import BadgePrint
from tests.conftest import authed_client, make_token, make_user


# ── Helpers (mirror test_visits.py) ─────────────────────────────────────────-

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


def _visit_payload(visitor_id: str, host_id: str, *, area: str = "office") -> dict:
    arrival = datetime.now(timezone.utc) + timedelta(days=1)
    return {
        "visitor_id": visitor_id,
        "host_id": host_id,
        "visit_date": arrival.date().isoformat(),
        "planned_arrival": arrival.isoformat(),
        "planned_departure": (arrival + timedelta(hours=2)).isoformat(),
        "visit_purpose": "meeting",
        "access_area": area,
    }


async def _create_visit(client, host_id: str, area: str = "office") -> dict:
    visitor_id = await _make_visitor(client)
    resp = await client.post(
        "/api/v1/visits",
        json=_visit_payload(visitor_id, host_id, area=area),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Happy path: first print = check-in ──────────────────────────────────────-

@pytest.mark.asyncio
async def test_first_print_checks_in_and_records_arrival(requester):
    user, client = requester
    visit = await _create_visit(client, str(user.id))
    assert visit["status"] == "confirmed"
    assert visit["actual_arrival"] is None

    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "checked_in"
    assert body["actual_arrival"] is not None


@pytest.mark.asyncio
async def test_first_print_inserts_exactly_one_badge_print_row(test_engine, requester):
    user, client = requester
    visit = await _create_visit(client, str(user.id))

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        before = (await db.execute(select(func.count(BadgePrint.id)))).scalar_one()

    await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )

    async with factory() as db:
        after = (await db.execute(select(func.count(BadgePrint.id)))).scalar_one()
    assert after - before == 1


# ── Reprint flow ────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_second_print_without_reason_returns_422(requester):
    user, client = requester
    visit = await _create_visit(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )
    # Second print needs reprint_reason.
    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )
    assert resp.status_code == 422
    assert "reason" in resp.text.lower()


@pytest.mark.asyncio
async def test_reprint_with_reason_does_not_re_check_in(requester):
    user, client = requester
    visit = await _create_visit(client, str(user.id))
    first = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )
    first_arrival = first.json()["actual_arrival"]

    second = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard", "reprint_reason": "Damaged badge"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["actual_arrival"] == first_arrival  # untouched
    assert second.json()["status"] == "checked_in"           # untouched


# ── Negative status gates ───────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_cannot_print_cancelled_visit(requester):
    user, client = requester
    visit = await _create_visit(client, str(user.id))
    await client.post(f"/api/v1/visits/{visit['id']}/cancel")
    resp = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )
    assert resp.status_code == 422
    assert "cancelled" in resp.text.lower()


# ── Scope: cannot print someone else's visit ────────────────────────────────-

@pytest.mark.asyncio
async def test_cannot_print_others_visit_returns_404(test_engine):
    user_a = await make_user(test_engine, role="requester")
    user_b = await make_user(test_engine, role="requester")
    async with authed_client(make_token(user_a.id, "requester")) as ca, \
               authed_client(make_token(user_b.id, "requester")) as cb:
        visit = await _create_visit(ca, str(user_a.id))
        resp = await cb.post(
            f"/api/v1/visits/{visit['id']}/print-badge",
            json={"template_used": "standard"},
        )
    assert resp.status_code == 404  # don't leak existence


# ── Audit log ───────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_first_print_writes_check_in_audit_event(test_engine, requester):
    user, client = requester
    visit = await _create_visit(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.entity_id == uuid.UUID(visit["id"]))
                .where(AuditLog.action_type == "visit.check_in")
                .order_by(AuditLog.id.desc()).limit(1)
            )
        ).scalar_one()
    assert row.user_id == user.id
    assert row.user_name == user.full_name
    assert (row.new_value or {}).get("status") == "checked_in"
    assert (row.new_value or {}).get("actual_arrival") is not None


@pytest.mark.asyncio
async def test_reprint_writes_badge_reprint_audit_event(test_engine, requester):
    user, client = requester
    visit = await _create_visit(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )
    await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard", "reprint_reason": "Lost original"},
    )

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.entity_id == uuid.UUID(visit["id"]))
                .where(AuditLog.action_type == "badge.reprint")
                .order_by(AuditLog.id.desc()).limit(1)
            )
        ).scalar_one()
    assert row.notes == "Lost original"


# ── Badge history ───────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_badge_history_lists_chronologically(requester):
    user, client = requester
    visit = await _create_visit(client, str(user.id))
    await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )
    await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard", "reprint_reason": "Damaged"},
    )
    resp = await client.get(f"/api/v1/visits/{visit['id']}/badge-history")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    # First print has no reprint_reason; second one does.
    assert rows[0]["reprint_reason"] is None
    assert rows[1]["reprint_reason"] == "Damaged"


# ── Template endpoints ──────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_admin_can_upsert_template(admin):
    _, client = admin
    payload = {
        "name": "standard",
        "html": "<div>Hello {{name}}</div>",
        "css": ".x { color: red; }",
        "is_default": True,
    }
    resp = await client.put("/api/v1/badge/templates/standard", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_default"] is True

    listed = await client.get("/api/v1/badge/templates")
    assert listed.status_code == 200
    assert "standard" in listed.json()


@pytest.mark.asyncio
async def test_non_admin_cannot_upsert_template(requester):
    _, client = requester
    payload = {"name": "x", "html": "<x/>", "css": "", "is_default": False}
    resp = await client.put("/api/v1/badge/templates/x", json=payload)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_anyone_can_list_templates(requester):
    _, client = requester
    resp = await client.get("/api/v1/badge/templates")
    assert resp.status_code == 200
