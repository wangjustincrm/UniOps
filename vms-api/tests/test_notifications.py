"""Training email dispatch + compliance-task creation at CHECK-IN.

Training / PPE are post-entry steps: a visitor checks in and gets their badge
first, then receives PPE / training on-site. So the HR training heads-up email
and the training/PPE confirm tasks fire at **first badge print (check-in)** —
NOT at create and NOT at approval. Badge printing itself is never blocked on
training/PPE (the only remaining GMP pre-print gate is the health declaration).

We don't hit a real SMTP server — `_send_email` is monkey-patched to record
(to, subject) tuples. Approval + a passing health declaration are simulated via
raw SQL (the engine writes the terminal state via raw SQL in production).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

import app.db.session as session_module
from app.models.task_mirror import Task
from app.services import notifications as notif

from tests.conftest import authed_client, make_token, make_user


# ── Recorder fixture (replaces _send_email in this module) ──────────────────-

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
        "last_name":  f"Test{uuid.uuid4().hex[:4]}",
        "company_name": "NotifCo",
        "phone": "+1-555-9100",
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
        "visit_purpose": "audit",
        "access_area": area,
    }


async def _set_contacts(admin_client, training: str | None, ppe: str | None) -> None:
    await admin_client.put(
        "/api/v1/admin/notification-contacts",
        json={"training_email": training, "ppe_email": ppe},
    )


async def _approve_and_pass_health(visit_id: str) -> None:
    """Simulate approval-api's post-approve callback AND a passing health
    declaration — so the GMP/lab badge can print (health decl is the only
    remaining pre-print gate)."""
    async with session_module.AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE vms_visits SET status='confirmed', "
                "approval_status='approved', health_decl_status='passed' "
                "WHERE id = :id"
            ),
            {"id": uuid.UUID(visit_id)},
        )
        await db.commit()


async def _print_badge(client, visit_id: str):
    return await client.post(
        f"/api/v1/visits/{visit_id}/print-badge",
        json={"template_used": "standard", "reprint_reason": None},
    )


# ── Office / warehouse: never any notifications ─────────────────────────────-

@pytest.mark.asyncio
async def test_office_visit_sends_no_notifications(_capture_emails, admin, requester):
    _, admin_client = admin
    await _set_contacts(admin_client, "t@x.com", "p@x.com")

    user, client = requester
    visitor = await _make_visitor(client)
    resp = await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="office")
    )
    assert resp.status_code == 201
    # Office check-in fires nothing either.
    await _print_badge(client, resp.json()["id"])
    assert _capture_emails == []


@pytest.mark.asyncio
async def test_warehouse_visit_sends_no_notifications(_capture_emails, admin, requester):
    _, admin_client = admin
    await _set_contacts(admin_client, "t@x.com", "p@x.com")

    user, client = requester
    visitor = await _make_visitor(client)
    resp = await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="warehouse")
    )
    assert resp.status_code == 201
    await _print_badge(client, resp.json()["id"])
    assert _capture_emails == []


# ── GMP visit: training email fires at CHECK-IN, not at create or approval ──-

@pytest.mark.asyncio
async def test_gmp_training_email_fires_at_checkin(test_engine, _capture_emails, admin, requester):
    _, admin_client = admin
    await _set_contacts(admin_client, "training@royalmilk.com", "ppe@royalmilk.com")

    user, client = requester
    visitor = await _make_visitor(client)
    created = (await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="production_gmp")
    )).json()
    # Nothing at create.
    assert _capture_emails == []

    # Approve + pass health. Reading the approved visit must NOT fire the
    # training email anymore — it's no longer an approval-time side effect.
    await _approve_and_pass_health(created["id"])
    await client.get(f"/api/v1/visits/{created['id']}")
    assert [e for e in _capture_emails if e["to"] == "training@royalmilk.com"] == []

    # Check-in (first badge print) fires the training heads-up.
    _capture_emails.clear()
    resp = await _print_badge(client, created["id"])
    assert resp.status_code == 201, resp.text
    training = [e for e in _capture_emails if e["to"] == "training@royalmilk.com"]
    assert len(training) == 1
    assert "training" in training[0]["subject"].lower()
    # PPE contact is NOT auto-emailed — PPE is host opt-in, not area-driven.
    assert [e for e in _capture_emails if e["to"] == "ppe@royalmilk.com"] == []


# ── Laboratory: same check-in behavior ──────────────────────────────────────-

@pytest.mark.asyncio
async def test_laboratory_training_email_fires_at_checkin(test_engine, _capture_emails, admin, requester):
    _, admin_client = admin
    await _set_contacts(admin_client, "training@x.com", "ppe@x.com")

    user, client = requester
    visitor = await _make_visitor(client)
    created = (await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="laboratory")
    )).json()
    assert _capture_emails == []

    await _approve_and_pass_health(created["id"])
    _capture_emails.clear()
    resp = await _print_badge(client, created["id"])
    assert resp.status_code == 201, resp.text
    assert [e for e in _capture_emails if e["to"] == "training@x.com"]


# ── Partial config: only training set ───────────────────────────────────────-

@pytest.mark.asyncio
async def test_only_training_contact_configured(test_engine, _capture_emails, admin, requester):
    _, admin_client = admin
    await _set_contacts(admin_client, "training@x.com", None)

    user, client = requester
    visitor = await _make_visitor(client)
    created = (await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="production_gmp")
    )).json()
    assert _capture_emails == []

    await _approve_and_pass_health(created["id"])
    _capture_emails.clear()
    resp = await _print_badge(client, created["id"])
    assert resp.status_code == 201, resp.text
    training = [e for e in _capture_emails if e["to"] == "training@x.com"]
    assert len(training) == 1


# ── No contacts: visit + check-in still succeed ─────────────────────────────-

@pytest.mark.asyncio
async def test_no_contacts_still_creates_and_checks_in(test_engine, _capture_emails, admin, requester):
    _, admin_client = admin
    await _set_contacts(admin_client, None, None)

    user, client = requester
    visitor = await _make_visitor(client)
    resp = await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="production_gmp")
    )
    assert resp.status_code == 201

    # Check-in succeeds even with no contacts configured — no training/PPE
    # dispatch, and crucially NO 422 block on the badge. (The host
    # approval-result email is a separate, expected notification.)
    await _approve_and_pass_health(resp.json()["id"])
    printed = await _print_badge(client, resp.json()["id"])
    assert printed.status_code == 201, printed.text
    assert not [e for e in _capture_emails if "training" in e["subject"].lower()]


# ── First print is not blocked AND creates the confirm tasks ────────────────-

@pytest.mark.asyncio
async def test_gmp_first_print_not_blocked_and_creates_tasks(
    test_engine, _capture_emails, admin, requester,
):
    """The core fix: a GMP first print succeeds even when training/PPE are
    stale (no 422), and opens the training + PPE confirm tasks at check-in."""
    _, admin_client = admin
    # Contacts must map to real users so the tasks get an assignee.
    training_user = await make_user(test_engine, role="requester", email="trainer@x.com")
    ppe_user = await make_user(test_engine, role="requester", email="janitor@x.com")
    await _set_contacts(admin_client, "trainer@x.com", "janitor@x.com")

    user, client = requester
    visitor = await _make_visitor(client)
    created = (await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="production_gmp")
    )).json()
    await _approve_and_pass_health(created["id"])

    # No 422 — printing is no longer gated on training/PPE.
    resp = await _print_badge(client, created["id"])
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "checked_in"

    # Both confirm tasks were opened at check-in, assigned to the contacts.
    async with session_module.AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Task).where(Task.document_id == uuid.UUID(visitor))
        )).scalars().all()
    by_type = {t.document_type: t for t in rows}
    assert "vms_train" in by_type
    assert "vms_ppe" in by_type
    assert by_type["vms_train"].assigned_user_id == training_user.id
    assert by_type["vms_ppe"].assigned_user_id == ppe_user.id


# ── Audit log: no premature dispatch at create ──────────────────────────────-

@pytest.mark.asyncio
async def test_create_audit_log_has_no_premature_training_dispatch(
    test_engine, _capture_emails, admin, requester,
):
    """The visit.create audit row must NOT claim a training/PPE dispatch —
    those fire at check-in. A GMP visit with no host-requested PPE dispatches
    nothing at create, so `notes` is empty."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models.audit_log import AuditLog

    _, admin_client = admin
    await _set_contacts(admin_client, "t@x.com", "p@x.com")

    user, client = requester
    visitor = await _make_visitor(client)
    created = (await client.post(
        "/api/v1/visits", json=_payload(visitor, str(user.id), area="production_gmp")
    )).json()

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(
            select(AuditLog)
            .where(AuditLog.entity_id == uuid.UUID(created["id"]))
            .where(AuditLog.action_type == "visit.create")
            .order_by(AuditLog.id.desc()).limit(1)
        )).scalar_one()
    assert not row.notes or "training" not in row.notes


# ── Subject + body content sanity (fires at check-in) ───────────────────────-

@pytest.mark.asyncio
async def test_training_email_body_includes_visitor_and_visit_details(
    test_engine, _capture_emails, admin, requester,
):
    _, admin_client = admin
    await _set_contacts(admin_client, "training@x.com", "ppe@x.com")

    user, client = requester

    resp = await client.post("/api/v1/visitors", json={
        "first_name": "Ada",
        "last_name":  "Lovelace",
        "company_name": "Analytical Engine Co.",
        "phone": "+1-555-0001",
        "visitor_type": "auditor",
    })
    visitor_id = resp.json()["id"]

    created = (await client.post(
        "/api/v1/visits", json=_payload(visitor_id, str(user.id), area="production_gmp")
    )).json()

    await _approve_and_pass_health(created["id"])
    _capture_emails.clear()
    printed = await _print_badge(client, created["id"])
    assert printed.status_code == 201, printed.text

    training = [e for e in _capture_emails if e["to"] == "training@x.com"]
    assert len(training) == 1
    body = training[0]["body"]
    assert "Ada" in body
    assert "Lovelace" in body
    assert "Analytical Engine Co." in body
    assert "production gmp" in body.lower()
