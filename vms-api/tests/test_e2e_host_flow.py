"""End-to-end test: the full Host journey through the VMS S1 MVP.

Replaces the Playwright script from VMS_SPRINT.md W8 with an ASGI-level test
that hits the real FastAPI app, the real SQLAlchemy session, the real audit
log — no browser required. Stays deterministic and runs in CI alongside the
unit tests.

The flow under test mirrors PRD §4.1 (Standard Visitor Flow):

    1. Host creates a visit appointment (status=confirmed).
    2. Host prints the badge → atomically check-in (status=checked_in).
    3. Host scans the QR code on the badge → check-out (status=checked_out).
    4. Every step appears in the audit log; the dashboard counters reflect
       the on-site → departed transition.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit_log import AuditLog
from app.models.badge_print import BadgePrint


# ── Helpers ─────────────────────────────────────────────────────────────────-

async def _make_visitor(client, *, marker: str) -> dict:
    resp = await client.post("/api/v1/visitors", json={
        "first_name": "E2E",
        "last_name":  f"Visitor-{marker}",
        "company_name": "E2E Test Co",
        "phone": "+1-555-0100",
        "email": f"e2e-{marker}@example.com",
        "visitor_type": "supplier",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _visit_payload(visitor_id: str, host_id: str) -> dict:
    arrival = datetime.now(timezone.utc) + timedelta(hours=1)
    return {
        "visitor_id": visitor_id,
        "host_id": host_id,
        "visit_date": arrival.date().isoformat(),
        "planned_arrival": arrival.isoformat(),
        "planned_departure": (arrival + timedelta(hours=2)).isoformat(),
        "visit_purpose": "meeting",
        "access_area": "office",
    }


# ── The journey ─────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_full_host_flow(test_engine, requester):
    """Walk a single Host through the full S1 MVP closed loop end-to-end."""
    user, client = requester
    marker = uuid.uuid4().hex[:6]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        audit_before = (await db.execute(select(func.count(AuditLog.id)))).scalar_one()
        badge_before = (await db.execute(select(func.count(BadgePrint.id)))).scalar_one()

    # ── Step 1: create visitor + appointment ──────────────────────────────-
    visitor = await _make_visitor(client, marker=marker)

    create_resp = await client.post(
        "/api/v1/visits", json=_visit_payload(visitor["id"], str(user.id))
    )
    assert create_resp.status_code == 201, create_resp.text
    visit = create_resp.json()
    assert visit["status"] == "confirmed"
    assert visit["actual_arrival"] is None
    assert visit["actual_departure"] is None
    visit_id = visit["id"]

    # ── Step 2: list endpoint sees the freshly-created visit ──────────────-
    list_resp = await client.get("/api/v1/visits")
    assert list_resp.status_code == 200
    visits = list_resp.json()["items"]
    assert any(v["id"] == visit_id for v in visits)

    # ── Step 3: print badge → check-in atomic ─────────────────────────────-
    print_resp = await client.post(
        f"/api/v1/visits/{visit_id}/print-badge",
        json={"template_used": "standard"},
    )
    assert print_resp.status_code == 201, print_resp.text
    after_print = print_resp.json()
    assert after_print["status"] == "checked_in"
    assert after_print["actual_arrival"] is not None

    # On-site dashboard reflects the new visitor immediately.
    overview_resp = await client.get("/api/v1/dashboard/overview")
    assert overview_resp.status_code == 200
    overview = overview_resp.json()
    assert overview["on_site_count"] >= 1

    # Active visits list includes our visit.
    active_resp = await client.get("/api/v1/visits/active")
    assert active_resp.status_code == 200
    assert any(v["id"] == visit_id for v in active_resp.json())

    # ── Step 4: simulate QR scan → check-out ──────────────────────────────-
    # The frontend would scan the visit UUID off the printed badge and call
    # GET /visits/{id} first to confirm, then POST check-out. We do both.
    visit_lookup = await client.get(f"/api/v1/visits/{visit_id}")
    assert visit_lookup.status_code == 200
    assert visit_lookup.json()["status"] == "checked_in"

    checkout_resp = await client.post(
        f"/api/v1/visits/{visit_id}/check-out",
        json={"badge_returned": True, "ppe_returned": True, "notes": "E2E run"},
    )
    assert checkout_resp.status_code == 200, checkout_resp.text
    departed = checkout_resp.json()
    assert departed["status"] == "checked_out"
    assert departed["actual_departure"] is not None
    assert departed["badge_returned"] is True

    # Visit is no longer "on-site"; dashboard subtracts.
    active_after = (await client.get("/api/v1/visits/active")).json()
    assert all(v["id"] != visit_id for v in active_after)

    # ── Step 5: audit log captured every mutation ─────────────────────────-
    async with factory() as db:
        audit_after = (await db.execute(select(func.count(AuditLog.id)))).scalar_one()
        badge_after = (await db.execute(select(func.count(BadgePrint.id)))).scalar_one()
        events = (
            await db.execute(
                select(AuditLog.action_type)
                .where(AuditLog.entity_id == uuid.UUID(visit_id))
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()

    # 4 events per visit: visit.create + visit.check_in + visit.check_out.
    # (visitor.create writes a separate audit row keyed to the visitor id.)
    assert "visit.create"    in events
    assert "visit.check_in"  in events
    assert "visit.check_out" in events
    assert audit_after - audit_before >= 4   # visitor.create + the three visit.* events
    assert badge_after - badge_before == 1   # exactly one badge print


@pytest.mark.asyncio
async def test_e2e_cancel_path(requester):
    """Alternate ending: Host cancels before check-in. Status terminates as
    `cancelled`; badge cannot subsequently print."""
    user, client = requester
    visitor = await _make_visitor(client, marker=uuid.uuid4().hex[:6])
    visit = (
        await client.post("/api/v1/visits", json=_visit_payload(visitor["id"], str(user.id)))
    ).json()

    cancel = await client.post(f"/api/v1/visits/{visit['id']}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"

    print_resp = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard"},
    )
    assert print_resp.status_code == 422
    assert "cancelled" in print_resp.text.lower()


@pytest.mark.asyncio
async def test_e2e_reprint_does_not_re_check_in(requester):
    """After check-in, a reprint with reason produces a second badge_prints
    row but does NOT reset `actual_arrival` or `status`."""
    user, client = requester
    visitor = await _make_visitor(client, marker=uuid.uuid4().hex[:6])
    visit = (
        await client.post("/api/v1/visits", json=_visit_payload(visitor["id"], str(user.id)))
    ).json()

    first = (
        await client.post(
            f"/api/v1/visits/{visit['id']}/print-badge",
            json={"template_used": "standard"},
        )
    ).json()
    first_arrival = first["actual_arrival"]

    second = await client.post(
        f"/api/v1/visits/{visit['id']}/print-badge",
        json={"template_used": "standard", "reprint_reason": "Lost original"},
    )
    assert second.status_code == 201
    body = second.json()
    assert body["status"] == "checked_in"
    assert body["actual_arrival"] == first_arrival   # untouched

    # Both prints recorded in history.
    history = (
        await client.get(f"/api/v1/visits/{visit['id']}/badge-history")
    ).json()
    assert len(history) == 2
    assert history[1]["reprint_reason"] == "Lost original"
