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
from app.services.approval import make_visit_title, sync_status_from_approval
from app.models.visit import AccessArea, VisitStatus
from tests.conftest import authed_client, make_token, make_user


def test_make_visit_title_fits_document_number_column():
    # tasks / approval_events.document_number are varchar(40): a longer title
    # failed every submit with a 500 (prod, 2026-10-15).
    title = make_visit_title(first_name="Axel", last_name="Struzina", company="Malette Electric")
    assert len(title) <= 40
    assert title.startswith("VMS Visit — Axel Struzina (Malette")
    assert title.endswith("…")
    long = make_visit_title(first_name="A", last_name="B", company="X" * 500)
    assert len(long) <= 40 and long.startswith("VMS Visit — A B")


def test_make_visit_title_short_is_unchanged():
    assert make_visit_title(first_name="Ann", last_name="Lee", company="Acme") == "VMS Visit — Ann Lee (Acme)"


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
async def test_zone_without_managers_needs_no_approval(requester):
    """A zone with no managers (every seeded zone until an admin sets them)
    does not engage approval-api: the visit is confirmed on booking."""
    user, client = requester
    visitor = await _make_visitor(client)
    for area in ("office", "warehouse", "production_gmp"):
        resp = await client.post(
            "/api/v1/visits", json=_payload(visitor, str(user.id), area=area),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "confirmed", area
        assert body["approval_status"] is None
        assert body["zone_approver_ids"] == []


@pytest.mark.asyncio
async def test_zone_with_managers_goes_through_approval(test_engine, requester, zone_managers):
    """Every active manager of the zone is snapshotted onto the visit for the
    Zone Manager step BEFORE the submit; a deactivated one is left out."""
    m1 = await make_user(test_engine, role="auditor")  # any role can manage a zone
    m2 = await make_user(test_engine, role="opm")
    gone = await make_user(test_engine, role="requester")
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        from app.models.user_mirror import User
        (await db.get(User, gone.id)).is_active = False
        await db.commit()
    await zone_managers("warehouse", [m1.id, gone.id, m2.id])

    user, client = requester
    visitor = await _make_visitor(client)
    resp = await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="warehouse"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending_approval"
    assert body["approval_status"] == "submitted"   # stub flipped draft→submitted
    assert sorted(body["zone_approver_ids"]) == sorted([str(m1.id), str(m2.id)])
    assert body["visit_title"].startswith("VMS Visit — Approval")


@pytest.mark.asyncio
async def test_zone_whose_managers_all_left_blocks_instead_of_skipping(
    test_engine, requester, zone_managers,
):
    """Managers configured but none active: the visit must NOT book without
    approval — it stays Pending Approval (draft) with an error to act on."""
    gone = await make_user(test_engine, role="requester")
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        from app.models.user_mirror import User
        (await db.get(User, gone.id)).is_active = False
        await db.commit()
    await zone_managers("laboratory", [gone.id])

    user, client = requester
    visitor = await _make_visitor(client)
    resp = await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="laboratory"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending_approval"
    assert body["approval_status"] == "draft"
    assert "No active manager" in body["approval_submit_error"]


@pytest.mark.asyncio
async def test_unknown_or_retired_zone_is_refused(test_engine, requester):
    user, client = requester
    visitor = await _make_visitor(client)
    resp = await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="nowhere"),
    )
    assert resp.status_code == 422

    admin = await make_user(test_engine, role="system_admin")
    async with authed_client(make_token(admin.id, "system_admin")) as ac:
        z = (await ac.post("/api/v1/admin/zones", json={"name": f"Old Shed {uuid.uuid4().hex[:4]}"})).json()
        assert (await ac.patch(f"/api/v1/admin/zones/{z['id']}", json={"is_active": False})).status_code == 200
    resp = await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area=z["code"]),
    )
    assert resp.status_code == 422
    # A retired zone still shows on area-rules, so old visits keep their label.
    rules = {r["area"]: r for r in (await client.get("/api/v1/area-rules")).json()}
    assert rules[z["code"]]["is_active"] is False


@pytest.mark.asyncio
async def test_zone_manager_can_open_the_visit(test_engine, requester, zone_managers):
    """A zone manager with no other claim on the visit can open it from the
    task-inbox deep link; a stranger cannot."""
    manager = await make_user(test_engine, role="requester")
    stranger = await make_user(test_engine, role="requester")
    await zone_managers("warehouse", [manager.id])
    user, client = requester
    visitor = await _make_visitor(client)
    created = (await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="warehouse"),
    )).json()
    async with authed_client(make_token(manager.id, "requester")) as mc:
        assert (await mc.get(f"/api/v1/visits/{created['id']}")).status_code == 200
        listed = (await mc.get("/api/v1/visits", params={"page_size": 100})).json()
        assert created["id"] in {v["id"] for v in listed["items"]}
    async with authed_client(make_token(stranger.id, "requester")) as sc:
        assert (await sc.get(f"/api/v1/visits/{created['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_confirmed_visit_may_only_move_to_a_zone_its_approvers_cover(
    test_engine, requester, zone_managers,
):
    m1 = await make_user(test_engine, role="requester")
    m2 = await make_user(test_engine, role="requester")
    await zone_managers("warehouse", [m1.id])
    user, client = requester
    visitor = await _make_visitor(client)
    created = (await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="warehouse"),
    )).json()
    await _force_approve(test_engine, created["id"])

    # Same manager owns the GMP zone → no new approval needed.
    await zone_managers("production_gmp", [m1.id])
    ok = await client.patch(f"/api/v1/visits/{created['id']}", json={"access_area": "production_gmp"})
    assert ok.status_code == 200, ok.text
    # Somebody new in charge of the lab → refused.
    await zone_managers("laboratory", [m1.id, m2.id])
    refused = await client.patch(f"/api/v1/visits/{created['id']}", json={"access_area": "laboratory"})
    assert refused.status_code == 409
    # A zone without managers → always fine.
    down = await client.patch(f"/api/v1/visits/{created['id']}", json={"access_area": "office"})
    assert down.status_code == 200


async def _force_approve(test_engine, visit_id: str) -> None:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(select(Visit).where(Visit.id == uuid.UUID(visit_id)))).scalar_one()
        row.approval_status = "approved"
        row.status = VisitStatus.confirmed
        row.host_notified_at = datetime.now(timezone.utc)
        await db.commit()


@pytest.mark.asyncio
async def test_cancel_pending_approval_cascades_to_engine(test_engine, requester, zone_managers):
    """Cancelling a pending-approval visit flips both status and approval_status."""
    await zone_managers("warehouse", [(await make_user(test_engine)).id])
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
async def test_print_badge_blocked_while_pending_approval(test_engine, requester, zone_managers):
    """Verifies the existing pending_approval gate on print-badge still
    works now that visits can naturally enter that state via approval."""
    await zone_managers("warehouse", [(await make_user(test_engine)).id])
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
async def test_admin_zone_crud(test_engine):
    admin = await make_user(test_engine, role="system_admin")
    m = await make_user(test_engine, role="requester")
    tag = uuid.uuid4().hex[:4]
    async with authed_client(make_token(admin.id, "system_admin")) as ac:
        listed = (await ac.get("/api/v1/admin/zones")).json()
        assert {"office", "warehouse", "all"} <= {z["code"] for z in listed}

        created = await ac.post("/api/v1/admin/zones", json={
            "name": f"Cold Room {tag}", "gmp_grade": True, "manager_ids": [str(m.id)],
        })
        assert created.status_code == 201, created.text
        z = created.json()
        assert z["code"] == f"cold_room_{tag}"
        assert [x["id"] for x in z["managers"]] == [str(m.id)]

        dup = await ac.post("/api/v1/admin/zones", json={"name": f"cold room {tag}"})
        assert dup.status_code == 409

        # Rename keeps the code; clearing managers makes it approval-free.
        upd = (await ac.patch(f"/api/v1/admin/zones/{z['id']}", json={
            "name": f"Cold Room {tag} B", "manager_ids": [],
        })).json()
        assert upd["code"] == z["code"] and upd["managers"] == []

        bad = await ac.patch(f"/api/v1/admin/zones/{z['id']}", json={"manager_ids": [str(uuid.uuid4())]})
        assert bad.status_code == 422

        assert (await ac.delete(f"/api/v1/admin/zones/{z['id']}")).status_code == 204

        # A zone in use cannot be deleted.
        office = next(x for x in listed if x["code"] == "office")
        requester_user = await make_user(test_engine)
        async with authed_client(make_token(requester_user.id, "requester")) as rc:
            vid = await _make_visitor(rc)
            await rc.post("/api/v1/visits", json=_payload(vid, str(requester_user.id), area="office"))
        assert (await ac.delete(f"/api/v1/admin/zones/{office['id']}")).status_code == 409


@pytest.mark.asyncio
async def test_non_admin_cannot_manage_zones(requester):
    _, client = requester
    assert (await client.post("/api/v1/admin/zones", json={"name": "X"})).status_code == 403
    assert (await client.get("/api/v1/admin/zones")).status_code == 403
