"""Host approval-result email (W12 / S2-D).

Read-side hook in `crud/visit.get_visit` fires `notify_host_approval_resolved`
exactly once per visit, when the read sees a transition from pending_approval
to a terminal user-facing status (confirmed or cancelled). `host_notified_at`
column tracks the send so subsequent reads no-op.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as session_module
from app.models.visit import Visit
from app.services import notifications as notif


# ── Email capture ───────────────────────────────────────────────────────────-

@pytest.fixture(autouse=True)
def _capture_emails(monkeypatch):
    sent: list[dict] = []

    async def _fake_send_email(*, cfg, to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(notif, "_send_email", _fake_send_email)
    return sent


# ── Helpers ─────────────────────────────────────────────────────────────────-

async def _make_visitor(client) -> str:
    resp = await client.post("/api/v1/visitors", json={
        "first_name": "Notif",
        "last_name":  f"Host{uuid.uuid4().hex[:4]}",
        "company_name": "NotifHostCo",
        "phone": "+1-555-7700",
        "visitor_type": "supplier",
    })
    return resp.json()["id"]


def _payload(visitor_id: str, host_id: str, *, area: str = "warehouse") -> dict:
    arrival = datetime.now(timezone.utc) + timedelta(days=1)
    return {
        "visitor_id": visitor_id,
        "host_id": host_id,
        "visit_date": arrival.date().isoformat(),
        "planned_arrival": arrival.isoformat(),
        "visit_purpose": "meeting",
        "access_area": area,
    }


async def _engine_approve(visit_id: str) -> None:
    """Simulate approval-api's `_post_approve_vms_visit` callback firing
    after all approval steps clear. Updates the shared DB row directly."""
    async with session_module.AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE vms_visits SET status='confirmed', "
                "approval_status='approved' WHERE id = :id"
            ),
            {"id": uuid.UUID(visit_id)},
        )
        await db.commit()


async def _engine_reject(visit_id: str) -> None:
    """Simulate approval-api setting approval_status='rejected'. The user-facing
    `status` stays pending_approval until vms-api's read-side sync mirrors it."""
    async with session_module.AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE vms_visits SET approval_status='rejected' WHERE id = :id"),
            {"id": uuid.UUID(visit_id)},
        )
        await db.commit()


# ── Happy path: approval clears → Host gets one email ───────────────────────-

@pytest.mark.asyncio
async def test_approval_resolves_to_confirmed_fires_one_host_email(
    test_engine, _capture_emails, requester,
):
    user, client = requester
    visitor = await _make_visitor(client)
    created = (
        await client.post("/api/v1/visits", json=_payload(visitor, str(user.id)))
    ).json()
    _capture_emails.clear()   # ignore the visit-create training/PPE emails

    # Engine clears approval out-of-band.
    await _engine_approve(created["id"])

    # First read after the transition triggers the email.
    resp = await client.get(f"/api/v1/visits/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"
    assert resp.json()["host_notified_at"] is not None

    host_emails = [e for e in _capture_emails if user.email in e["to"]]
    assert len(host_emails) == 1
    assert "approved" in host_emails[0]["subject"].lower()


@pytest.mark.asyncio
async def test_engine_rejection_fires_one_host_email(
    test_engine, _capture_emails, requester,
):
    user, client = requester
    visitor = await _make_visitor(client)
    created = (
        await client.post("/api/v1/visits", json=_payload(visitor, str(user.id)))
    ).json()
    _capture_emails.clear()

    await _engine_reject(created["id"])

    resp = await client.get(f"/api/v1/visits/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"   # synced from rejected
    assert resp.json()["host_notified_at"] is not None

    host_emails = [e for e in _capture_emails if user.email in e["to"]]
    assert len(host_emails) == 1
    assert "rejected" in host_emails[0]["subject"].lower()


# ── Idempotency ────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_second_read_does_not_re_fire_host_email(
    test_engine, _capture_emails, requester,
):
    user, client = requester
    visitor = await _make_visitor(client)
    created = (
        await client.post("/api/v1/visits", json=_payload(visitor, str(user.id)))
    ).json()
    await _engine_approve(created["id"])
    _capture_emails.clear()

    # First read fires the email.
    await client.get(f"/api/v1/visits/{created['id']}")
    n1 = len(_capture_emails)
    assert n1 == 1

    # Second read should NOT.
    await client.get(f"/api/v1/visits/{created['id']}")
    n2 = len(_capture_emails)
    assert n2 == n1


# ── Negative cases ─────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_office_visit_does_not_fire_host_email(
    _capture_emails, requester,
):
    """Office visits never go through approval, so no Host email."""
    user, client = requester
    visitor = await _make_visitor(client)
    created = (
        await client.post(
            "/api/v1/visits", json=_payload(visitor, str(user.id), area="office")
        )
    ).json()
    _capture_emails.clear()

    await client.get(f"/api/v1/visits/{created['id']}")
    host_emails = [e for e in _capture_emails if user.email in e["to"]]
    assert host_emails == []


@pytest.mark.asyncio
async def test_visit_still_pending_does_not_fire_email(
    _capture_emails, requester,
):
    user, client = requester
    visitor = await _make_visitor(client)
    created = (
        await client.post("/api/v1/visits", json=_payload(visitor, str(user.id)))
    ).json()
    _capture_emails.clear()

    # Don't transition the engine yet. Read should NOT fire.
    await client.get(f"/api/v1/visits/{created['id']}")
    host_emails = [e for e in _capture_emails if user.email in e["to"]]
    assert host_emails == []
