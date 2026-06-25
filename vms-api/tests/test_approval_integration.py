"""vms-api side of the approval integration (PRD §6.2.1 / S2-B / W10).

Verifies the contract vms-api delivers to approval-api BEFORE the HTTP
submit, plus the read-side state sync. The stubbed `approval_svc.submit_*`
helpers (see conftest._stub_approval_engine) simulate what the engine would
write back so we can assert the round-trip from vms-api's perspective
without running approval-api as a separate service.

A full end-to-end test (real approval-api process) is a separate harness;
this file covers everything vms-api owns.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.visit import Visit
from app.services.approval import (
    access_requires_approval, access_requires_quality_manager,
    make_visit_title, pick_quality_manager, sync_status_from_approval,
)
from app.models.visit import AccessArea, VisitStatus
from tests.conftest import authed_client, make_token, make_user


# ── Pure helper unit tests (no DB) ──────────────────────────────────────────-

def test_office_does_not_require_approval():
    assert access_requires_approval(AccessArea.office) is False
    assert access_requires_quality_manager(AccessArea.office) is False


def test_warehouse_requires_approval_but_not_qm():
    assert access_requires_approval(AccessArea.warehouse) is True
    assert access_requires_quality_manager(AccessArea.warehouse) is False


@pytest.mark.parametrize("area", [
    AccessArea.production_gmp, AccessArea.laboratory, AccessArea.all,
])
def test_gmp_lab_all_require_both_approval_and_qm(area):
    assert access_requires_approval(area) is True
    assert access_requires_quality_manager(area) is True


def test_make_visit_title_truncates_to_255():
    long_company = "X" * 500
    title = make_visit_title(first_name="A", last_name="B", company=long_company)
    assert len(title) <= 255
    assert title.startswith("VMS Visit — A B")


def test_pick_quality_manager_first_active():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    roster = [str(a), str(b), str(c)]
    active = {b, c}        # `a` is deactivated
    assert pick_quality_manager(roster=roster, active_user_ids=active) == b


def test_pick_quality_manager_returns_none_when_roster_empty():
    assert pick_quality_manager(roster=[], active_user_ids=set()) is None


def test_pick_quality_manager_returns_none_when_no_one_active():
    a = uuid.uuid4()
    assert pick_quality_manager(roster=[str(a)], active_user_ids=set()) is None


def test_pick_quality_manager_skips_malformed_entries():
    good = uuid.uuid4()
    roster = ["not-a-uuid", "", str(good)]
    assert pick_quality_manager(roster=roster, active_user_ids={good}) == good


# ── Read-side status sync ───────────────────────────────────────────────────-

def _visit(status: VisitStatus, approval_status: str | None) -> Visit:
    return Visit(
        id=uuid.uuid4(),
        visitor_id=uuid.uuid4(),
        host_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        visit_date=datetime.now(timezone.utc).date(),
        planned_arrival=datetime.now(timezone.utc),
        visit_purpose="meeting",  # type: ignore[arg-type]
        access_area=AccessArea.production_gmp,
        status=status,
        approval_status=approval_status,
        visit_title="",
        badge_returned=False,
        safety_training_confirmed=False,
    )


def test_sync_noop_when_not_pending_approval():
    v = _visit(VisitStatus.confirmed, "approved")
    sync_status_from_approval(v)
    assert v.status == VisitStatus.confirmed   # untouched


def test_sync_flips_approved_to_confirmed():
    v = _visit(VisitStatus.pending_approval, "approved")
    sync_status_from_approval(v)
    assert v.status == VisitStatus.confirmed


@pytest.mark.parametrize("rejected_state", ["rejected", "cancelled"])
def test_sync_flips_rejected_or_cancelled_to_cancelled(rejected_state):
    v = _visit(VisitStatus.pending_approval, rejected_state)
    sync_status_from_approval(v)
    assert v.status == VisitStatus.cancelled


def test_sync_leaves_pending_approval_alone_when_engine_still_running():
    v = _visit(VisitStatus.pending_approval, "in_review")
    sync_status_from_approval(v)
    assert v.status == VisitStatus.pending_approval


# ── End-to-end through the visits API (uses stubbed submit) ────────────────-

async def _make_visitor(client) -> str:
    resp = await client.post("/api/v1/visitors", json={
        "first_name": "Approval",
        "last_name":  f"Test{uuid.uuid4().hex[:4]}",
        "company_name": "ApprovalCo",
        "phone": "+1-555-7000",
        "visitor_type": "supplier",
    })
    return resp.json()["id"]


def _payload(visitor_id: str, host_id: str, *, area: str) -> dict:
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


@pytest.mark.asyncio
async def test_office_visit_bypasses_approval_entirely(requester):
    """Office visits do not engage approval-api; status stays at confirmed
    and approval_status stays NULL."""
    user, client = requester
    visitor = await _make_visitor(client)
    resp = await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="office"),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["approval_status"] is None


@pytest.mark.asyncio
async def test_warehouse_visit_goes_through_approval_no_qm(requester):
    """Warehouse triggers approval-api submit but no Quality Manager pre-assignment."""
    user, client = requester
    visitor = await _make_visitor(client)
    resp = await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="warehouse"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending_approval"
    assert body["approval_status"] == "submitted"   # stub flipped draft→submitted
    assert body["quality_approver_id"] is None
    assert body["visit_title"].startswith("VMS Visit — Approval")


@pytest.mark.asyncio
async def test_gmp_visit_pre_assigns_quality_manager(test_engine, requester):
    """GMP visit picks a QM from `vms_config.quality_manager_user_ids` and
    writes it to `quality_approver_id` BEFORE submitting."""
    # Seed the roster with one user.
    qm_user = await make_user(test_engine, role="auditor")  # any role works as a QM
    # Set the roster via the admin API — needs an admin caller.
    admin_user = await make_user(test_engine, role="system_admin")
    async with authed_client(make_token(admin_user.id, "system_admin")) as ac:
        await ac.put(
            "/api/v1/admin/quality-managers",
            json={"user_ids": [str(qm_user.id)]},
        )

    # Now a requester creates a GMP visit.
    user, client = requester
    visitor = await _make_visitor(client)
    resp = await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="production_gmp"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending_approval"
    assert body["approval_status"] == "submitted"
    assert body["quality_approver_id"] == str(qm_user.id)


@pytest.mark.asyncio
async def test_cancel_pending_approval_cascades_to_engine(test_engine, requester):
    """Cancelling a pending-approval visit flips both status and approval_status."""
    user, client = requester
    visitor = await _make_visitor(client)
    created = (
        await client.post(
            "/api/v1/visits", json=_payload(visitor, str(user.id), area="warehouse"),
        )
    ).json()
    assert created["status"] == "pending_approval"

    resp = await client.post(f"/api/v1/visits/{created['id']}/cancel")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "cancelled"

    # Engine state was also cleaned (via stub).
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(
            select(Visit).where(Visit.id == uuid.UUID(created["id"]))
        )).scalar_one()
    assert row.approval_status == "cancelled"


@pytest.mark.asyncio
async def test_print_badge_blocked_while_pending_approval(requester):
    """Verifies the existing pending_approval gate on print-badge still
    works now that visits can naturally enter that state via approval."""
    user, client = requester
    visitor = await _make_visitor(client)
    created = (
        await client.post(
            "/api/v1/visits", json=_payload(visitor, str(user.id), area="warehouse"),
        )
    ).json()
    # Stubbed engine flipped to submitted, but our read-side sync only flips
    # on terminal states — so the visit is still in pending_approval.
    resp = await client.post(
        f"/api/v1/visits/{created['id']}/print-badge",
        json={"template_used": "standard"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_quality_manager_roster_get_and_put(test_engine):
    """PUT replaces atomically; subsequent GET reflects the change.

    We don't assert anything about the initial roster state — other tests in
    the session may have populated it. Both PUT calls in this test do explicit
    overwrites, so the assertions don't depend on starting state.
    """
    admin = await make_user(test_engine, role="system_admin")
    a, b = uuid.uuid4(), uuid.uuid4()

    async with authed_client(make_token(admin.id, "system_admin")) as ac:
        # Empty out first so the next assertions are deterministic.
        await ac.put("/api/v1/admin/quality-managers", json={"user_ids": []})
        empty = await ac.get("/api/v1/admin/quality-managers")
        assert empty.status_code == 200
        assert empty.json()["user_ids"] == []

        # Replace with two ids.
        resp = await ac.put(
            "/api/v1/admin/quality-managers",
            json={"user_ids": [str(a), str(b)]},
        )
        assert resp.status_code == 200
        assert sorted(resp.json()["user_ids"]) == sorted([str(a), str(b)])


@pytest.mark.asyncio
async def test_non_admin_cannot_set_roster(requester):
    _, client = requester
    resp = await client.put(
        "/api/v1/admin/quality-managers",
        json={"user_ids": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 403
