"""Approval lifecycle edges that used to leak (VMS PRD V2.7 §12).

D-01  approval hand-off fails  → visit stays Pending Approval ("draft"), not Confirmed
D-02  access-area edit         → cannot move a visit into an area it is not approved for
D-03  "Return for edit"        → Host edits and resubmits; the Revise task closes
D-09  cancel                   → Host hears "cancelled", never "rejected by an approver"
D-12  pending past arrival     → No Show, approval closed, tasks completed
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as session_module
from app.models.approval_event_mirror import ApprovalEvent
from app.models.task_mirror import Task
from app.models.visit import Visit, VisitStatus
from app.services import approval as approval_svc
from app.services import notifications as notif
from app.services.scheduled_jobs import mark_no_shows
from tests.conftest import authed_client, make_token, make_user


@pytest.fixture(autouse=True)
def _capture_emails(monkeypatch):
    sent: list[dict] = []

    async def _fake_send_email(*, cfg, to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(notif, "_send_email", _fake_send_email)
    return sent


def _session():
    return session_module.AsyncSessionLocal()


async def _visitor(client) -> str:
    resp = await client.post("/api/v1/visitors", json={
        "first_name": "Life", "last_name": f"Cycle{uuid.uuid4().hex[:4]}",
        "company_name": "LifeCo", "visitor_type": "supplier",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _payload(visitor_id: str, host_id, *, area: str, arrival: datetime | None = None) -> dict:
    arrival = arrival or datetime.now(timezone.utc) + timedelta(days=1)
    return {
        "visitor_id": visitor_id, "host_id": str(host_id),
        "visit_date": arrival.date().isoformat(), "planned_arrival": arrival.isoformat(),
        "visit_purpose": "meeting", "access_area": area,
    }


async def _set(visit_id, **fields):
    async with _session() as db:
        row = (await db.execute(select(Visit).where(Visit.id == uuid.UUID(str(visit_id))))).scalar_one()
        for k, v in fields.items():
            setattr(row, k, v)
        await db.commit()


async def _get(visit_id) -> Visit:
    async with _session() as db:
        return (await db.execute(select(Visit).where(Visit.id == uuid.UUID(str(visit_id))))).scalar_one()


async def _add_task(visit_id, *, task_type: str, assignee) -> uuid.UUID:
    async with _session() as db:
        t = Task(type=task_type, document_type="vms_visit", document_id=uuid.UUID(str(visit_id)),
                 document_number="x", assigned_role="requester", assigned_user_id=assignee.id,
                 title=task_type)
        db.add(t)
        await db.commit()
        return t.id


async def _open_task_types(visit_id) -> list[str]:
    async with _session() as db:
        return sorted((await db.execute(select(Task.type).where(
            Task.document_id == uuid.UUID(str(visit_id)), Task.is_completed.is_(False)))).scalars().all())


# ── D-01 ─────────────────────────────────────────────────────────────────────

async def test_failed_hand_off_keeps_visit_pending_and_resubmit_works(requester, monkeypatch):
    user, client = requester
    vid = await _visitor(client)

    async def _boom(visit_id, bearer_token):
        raise RuntimeError("approval-api unreachable")

    monkeypatch.setattr(approval_svc, "submit_for_approval", _boom)
    resp = await client.post("/api/v1/visits", json=_payload(vid, user.id, area="production_gmp"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending_approval"
    assert body["approval_status"] == "draft"
    assert "approval-api unreachable" in body["approval_submit_error"]

    # Cannot be badged — the old behaviour confirmed it and let it print.
    pr = await client.post(f"/api/v1/visits/{body['id']}/print-badge", json={})
    assert pr.status_code in (409, 422), pr.text

    monkeypatch.undo()  # back to the conftest stub (draft → submitted)
    monkeypatch.setattr(notif, "_send_email", _noop_send)
    rs = await client.post(f"/api/v1/visits/{body['id']}/submit")
    assert rs.status_code == 200, rs.text
    assert rs.json()["approval_status"] == "submitted"
    assert rs.json()["status"] == "pending_approval"


async def _noop_send(*, cfg, to, subject, body):
    return True


async def test_submit_refused_while_approval_in_flight(requester):
    user, client = requester
    vid = await _visitor(client)
    v = (await client.post("/api/v1/visits", json=_payload(vid, user.id, area="warehouse"))).json()
    assert v["approval_status"] == "submitted"
    resp = await client.post(f"/api/v1/visits/{v['id']}/submit")
    assert resp.status_code == 409


# ── D-02 ─────────────────────────────────────────────────────────────────────

async def test_confirmed_office_visit_cannot_be_moved_into_gmp(requester):
    user, client = requester
    vid = await _visitor(client)
    v = (await client.post("/api/v1/visits", json=_payload(vid, user.id, area="office"))).json()
    assert v["status"] == "confirmed"

    resp = await client.patch(f"/api/v1/visits/{v['id']}", json={"access_area": "production_gmp"})
    assert resp.status_code == 409, resp.text
    assert (await _get(v["id"])).access_area.value == "office"


async def test_approved_gmp_visit_can_move_down_to_office(requester):
    user, client = requester
    vid = await _visitor(client)
    v = (await client.post("/api/v1/visits", json=_payload(vid, user.id, area="production_gmp"))).json()
    await _set(v["id"], status=VisitStatus.confirmed, approval_status="approved",
               host_notified_at=datetime.now(timezone.utc))

    resp = await client.patch(f"/api/v1/visits/{v['id']}", json={"access_area": "office"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_area"] == "office"


async def test_area_locked_while_approval_in_flight(requester):
    user, client = requester
    vid = await _visitor(client)
    v = (await client.post("/api/v1/visits", json=_payload(vid, user.id, area="warehouse"))).json()
    resp = await client.patch(f"/api/v1/visits/{v['id']}", json={"access_area": "office"})
    assert resp.status_code == 409


# ── D-03 ─────────────────────────────────────────────────────────────────────

async def test_returned_visit_edited_and_resubmitted_by_host(test_engine, requester):
    creator, creator_client = requester
    host = await make_user(test_engine, role="requester")
    approver = await make_user(test_engine, role="dept_manager", full_name="Ann Approver")
    vid = await _visitor(creator_client)
    v = (await creator_client.post("/api/v1/visits", json=_payload(vid, host.id, area="warehouse"))).json()

    # Simulate the engine's "return": state + revise task + event.
    await _set(v["id"], approval_status="returned", approval_step_idx=0)
    await _add_task(v["id"], task_type="revise_vms_visit", assignee=creator)
    async with _session() as db:
        db.add(ApprovalEvent(document_type="vms_visit", document_id=uuid.UUID(v["id"]),
                             document_number="x", action="return", actor_id=approver.id,
                             actor_role="dept_manager", comment="Wrong day — make it Friday"))
        await db.commit()

    async with authed_client(make_token(host.id, host.role)) as host_client:
        detail = (await host_client.get(f"/api/v1/visits/{v['id']}")).json()
        assert detail["status"] == "pending_approval"
        assert detail["approval_note"]["comment"] == "Wrong day — make it Friday"
        assert detail["approval_note"]["actor_name"] == "Ann Approver"

        # The Host (not the creator) may edit and resubmit.
        ed = await host_client.patch(f"/api/v1/visits/{v['id']}", json={"access_area": "production_gmp"})
        assert ed.status_code == 200, ed.text
        rs = await host_client.post(f"/api/v1/visits/{v['id']}/submit")
        assert rs.status_code == 200, rs.text
        assert rs.json()["approval_status"] == "submitted"

    assert await _open_task_types(v["id"]) == []  # Revise task closed


async def test_returned_visit_moved_to_office_is_confirmed_without_approval(requester, _capture_emails):
    user, client = requester
    vid = await _visitor(client)
    v = (await client.post("/api/v1/visits", json=_payload(vid, user.id, area="warehouse"))).json()
    await _set(v["id"], approval_status="returned")
    await _add_task(v["id"], task_type="revise_vms_visit", assignee=user)

    assert (await client.patch(f"/api/v1/visits/{v['id']}", json={"access_area": "office"})).status_code == 200
    rs = await client.post(f"/api/v1/visits/{v['id']}/submit")
    assert rs.status_code == 200, rs.text
    assert rs.json()["status"] == "confirmed"
    assert rs.json()["approval_status"] is None
    assert await _open_task_types(v["id"]) == []
    # Re-reading must not fire an "approved"/"rejected" email.
    await client.get(f"/api/v1/visits/{v['id']}")
    assert not [m for m in _capture_emails if "approved" in m["subject"] or "rejected" in m["subject"]]


# ── D-09 + Host may cancel ───────────────────────────────────────────────────

async def test_host_who_did_not_book_can_cancel_and_tasks_close(test_engine, requester, _capture_emails):
    creator, creator_client = requester
    host = await make_user(test_engine, role="requester")
    approver = await make_user(test_engine, role="dept_manager")
    vid = await _visitor(creator_client)
    v = (await creator_client.post("/api/v1/visits", json=_payload(vid, host.id, area="warehouse"))).json()
    await _add_task(v["id"], task_type="approve_vms_visit", assignee=approver)

    async with authed_client(make_token(host.id, host.role)) as host_client:
        resp = await host_client.post(f"/api/v1/visits/{v['id']}/cancel")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"
        await host_client.get(f"/api/v1/visits/{v['id']}")  # read-side hook must stay quiet

    assert await _open_task_types(v["id"]) == []
    assert (await _get(v["id"])).approval_status == "cancelled"
    assert not [m for m in _capture_emails if "rejected" in m["subject"]]
    assert not [m for m in _capture_emails if m["to"] == host.email]  # the Host cancelled it


async def test_creator_cancel_tells_host_cancelled_not_rejected(test_engine, requester, _capture_emails):
    creator, client = requester
    host = await make_user(test_engine, role="requester")
    vid = await _visitor(client)
    v = (await client.post("/api/v1/visits", json=_payload(vid, host.id, area="warehouse"))).json()

    assert (await client.post(f"/api/v1/visits/{v['id']}/cancel")).status_code == 200
    await client.get(f"/api/v1/visits/{v['id']}")

    to_host = [m["subject"] for m in _capture_emails if m["to"] == host.email]
    assert to_host and all("cancelled" in s for s in to_host)
    assert not [s for s in to_host if "rejected" in s]


async def test_auditor_still_cannot_cancel(requester, auditor):
    user, client = requester
    _a, auditor_client = auditor
    vid = await _visitor(client)
    v = (await client.post("/api/v1/visits", json=_payload(vid, user.id, area="office"))).json()
    assert (await auditor_client.post(f"/api/v1/visits/{v['id']}/cancel")).status_code == 403


# ── D-12 ─────────────────────────────────────────────────────────────────────

async def test_pending_visit_past_arrival_becomes_no_show_and_tasks_close(requester, test_engine):
    user, client = requester
    approver = await make_user(test_engine, role="dept_manager")
    vid = await _visitor(client)
    arrival = datetime.now(timezone.utc) - timedelta(hours=3)
    v = (await client.post("/api/v1/visits", json=_payload(vid, user.id, area="warehouse", arrival=arrival))).json()
    await _add_task(v["id"], task_type="approve_vms_visit", assignee=approver)

    async with _session() as db:
        marked = await mark_no_shows(db)
        await db.commit()
    assert uuid.UUID(v["id"]) in marked
    row = await _get(v["id"])
    assert row.status == VisitStatus.no_show
    assert row.approval_status == "cancelled"
    assert await _open_task_types(v["id"]) == []
