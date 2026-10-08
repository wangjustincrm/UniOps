"""The Approval Timeline's "Send reminder" button actually sends something.

Before this suite existed the button was wired to `console.log`: it produced no
request, no email, and no feedback, so a user who clicked it believed the
approver had been nudged. These tests pin the four things that make the button
honest — it mails the person who is actually waiting, it refuses (visibly) when
there is nobody to reach, it will not mail the same approver twice a day, and
it never nudges the holder of a non-approval task on the same document.
"""
import copy
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select as sa_select, update as sa_update, text

import app.db.session as session_module
from app.core.background import drain
from app.core.delegation import local_today
from app.crud import user as user_crud
from app.crud.config import get_or_create as get_config
from app.models.config import CompanyConfig
from app.models.notification_log import NotificationLog
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.services.manual_reminder import MANUAL_REMINDER_TEMPLATE


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def captured_emails(monkeypatch):
    """(to, subject, html) for every email the dispatcher sends."""
    sent: list[tuple[str, str, str]] = []

    async def _fake_send_email(to, subject, html, **kwargs):
        sent.append((to, subject, html))

    # _dispatch imports send_email from app.services.email at call time.
    monkeypatch.setattr("app.services.email.send_email", _fake_send_email)
    return sent


@pytest.fixture(autouse=True)
async def _restore_notification_settings():
    """Snapshot/restore company_config.notification_settings.

    Same reasoning as test_notification_dispatch.py: get_or_create returns one
    shared row, so a default_channel this file sets would otherwise leak into
    every later test file in the session.
    """
    async with session_module.AsyncSessionLocal() as db:
        cfg = await get_config(db)
        snapshot = copy.deepcopy(cfg.notification_settings)
        await db.commit()
    yield
    async with session_module.AsyncSessionLocal() as db:
        await _write_notif_settings(db, copy.deepcopy(snapshot))


async def _write_notif_settings(db, value: dict | None) -> None:
    """Write to EVERY company_config row — earlier suites leave duplicates
    behind and get_or_create picks one without ORDER BY (see the long note in
    test_notification_dispatch.py)."""
    await db.execute(sa_update(CompanyConfig).values(notification_settings=value))
    await db.commit()


async def _set_notif_settings(db, **kv):
    cfg = await get_config(db)
    await _write_notif_settings(db, {**(cfg.notification_settings or {}), **kv})


# ── Builders ─────────────────────────────────────────────────────────────────

async def _user(db, role="dept_manager", name="Approving Manager"):
    u = await user_crud.create(db, RegisterRequest(
        email=f"rem-{uuid.uuid4().hex[:8]}@example.com",
        password="TestPass1!", full_name=name, role=role))
    await db.flush()
    return u


async def _pr(db, creator):
    pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:8]}", title="Reminder PR",
                         type=2, status="in_review", created_by=creator.id)
    db.add(pr)
    await db.flush()
    return pr


async def _po(db, creator):
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
                    contact_name="C", contact_email="c@x.com")
    db.add(vendor)
    await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="Reminder PO", type=2,
                       vendor_id=vendor.id, vendor_name="Acme", status="in_review",
                       created_by=creator.id)
    db.add(po)
    await db.flush()
    return po


async def _task(db, doc, doc_type, approver, *, task_type=None):
    task = Task(
        type=task_type or f"approve_{doc_type}",
        document_type=doc_type, document_id=doc.id, document_number=doc.number,
        assigned_role=approver.role if approver else "dept_manager",
        assigned_user_id=approver.id if approver else None,
        title=f"Approve {doc.number}",
    )
    db.add(task)
    await db.flush()
    return task


def _to(captured) -> list[str]:
    return [to for to, _, _ in captured]


# ── The happy path ───────────────────────────────────────────────────────────

async def test_reminder_emails_the_pending_approver(requester_client, captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        approver = await _user(db)
        pr = await _pr(db, approver)
        await _task(db, pr, "pr", approver)
        await db.commit()

    resp = await requester_client.post(f"/api/v1/pr/{pr.id}/remind")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sent"] is True
    assert body["document_number"] == pr.number
    # The confirmation names who it reached — a bare "sent" is what made the
    # old console.log button indistinguishable from a working one.
    assert body["recipients"] == ["Approving Manager"]

    await drain(5)
    assert approver.email in _to(captured_emails)
    subject = next(s for to, s, _ in captured_emails if to == approver.email)
    assert pr.number in subject
    html = next(h for to, _, h in captured_emails if to == approver.email)
    assert "Test requester" in html, "the email must name who sent the reminder"


async def test_po_reminder_uses_the_same_path(requester_client, captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        approver = await _user(db, role="procurement_manager", name="PO Approver")
        po = await _po(db, approver)
        await _task(db, po, "po", approver)
        await db.commit()

    resp = await requester_client.post(f"/api/v1/po/{po.id}/remind")
    assert resp.status_code == 200, resp.text
    assert resp.json()["recipients"] == ["PO Approver"]

    await drain(5)
    assert approver.email in _to(captured_emails)


async def test_reminder_reaches_the_stand_in_while_the_approver_is_away(
    requester_client, captured_emails,
):
    """Delegation is a substitution: mailing the delegator who is away is noise.
    The recipient list the caller sees must match who the mail actually goes to."""
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        approver = await _user(db, name="Away Manager")
        stand_in = await _user(db, name="Stand In")
        pr = await _pr(db, approver)
        await _task(db, pr, "pr", approver)
        # Window anchored on the PLANT date, which is what active_delegate_id
        # binds — a UTC date would expire the window early after 20:00 Toronto.
        today = local_today()
        await db.execute(text(
            "INSERT INTO approval_delegations (id, delegator_user_id, delegate_user_id,"
            " start_date, end_date, revoked_at, created_by)"
            " VALUES (:i, :a, :b, :s, :e, NULL, :a)"),
            {"i": uuid.uuid4(), "a": approver.id, "b": stand_in.id,
             "s": today - timedelta(days=1), "e": today + timedelta(days=1)},
        )
        await db.commit()

    resp = await requester_client.post(f"/api/v1/pr/{pr.id}/remind")
    assert resp.status_code == 200, resp.text
    assert resp.json()["recipients"] == ["Stand In"]

    await drain(5)
    assert stand_in.email in _to(captured_emails)
    assert approver.email not in _to(captured_emails)


# ── The refusals — every one of these used to be a silent no-op ──────────────

async def test_second_reminder_inside_the_cooldown_is_refused(
    requester_client, captured_emails,
):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        approver = await _user(db)
        pr = await _pr(db, approver)
        await _task(db, pr, "pr", approver)
        await db.commit()

    assert (await requester_client.post(f"/api/v1/pr/{pr.id}/remind")).status_code == 200
    second = await requester_client.post(f"/api/v1/pr/{pr.id}/remind")
    assert second.status_code == 429, second.text
    assert "already sent" in second.json()["detail"]

    await drain(5)
    assert _to(captured_emails).count(approver.email) == 1, \
        "the cooldown must survive a second click, not just dedupe the log"


async def test_cooldown_marker_is_written_before_the_send(requester_client, captured_emails):
    """The 'queued' row is what makes a double click safe: it lands in the same
    commit as the request, before the background dispatch even starts."""
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        approver = await _user(db)
        pr = await _pr(db, approver)
        task = await _task(db, pr, "pr", approver)
        await db.commit()

    assert (await requester_client.post(f"/api/v1/pr/{pr.id}/remind")).status_code == 200

    async with session_module.AsyncSessionLocal() as db:
        rows = (await db.execute(sa_select(NotificationLog).where(
            NotificationLog.task_id == task.id,
            NotificationLog.template_key == MANUAL_REMINDER_TEMPLATE,
        ))).scalars().all()
    assert any(r.status == "queued" for r in rows)


async def test_reminder_refused_when_nobody_is_waiting(requester_client, captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        approver = await _user(db)
        pr = await _pr(db, approver)          # no open task at all
        await db.commit()

    resp = await requester_client.post(f"/api/v1/pr/{pr.id}/remind")
    assert resp.status_code == 409
    assert "not waiting on an approver" in resp.json()["detail"]
    await drain(5)
    assert captured_emails == []


async def test_reminder_ignores_non_approval_tasks_on_the_same_document(
    requester_client, captured_emails,
):
    """A PO carries place_order / create_pa tasks long after approval is done.
    A link that sits under the *approval* timeline must never mail those
    holders — that would nudge the wrong person about the wrong thing."""
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        buyer = await _user(db, role="procurement_officer", name="Buyer")
        po = await _po(db, buyer)
        await _task(db, po, "po", buyer, task_type="place_order")
        await db.commit()

    resp = await requester_client.post(f"/api/v1/po/{po.id}/remind")
    assert resp.status_code == 409
    await drain(5)
    assert buyer.email not in _to(captured_emails)


async def test_reminder_refused_when_notifications_are_off_company_wide(
    requester_client, captured_emails,
):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="none")
        approver = await _user(db)
        pr = await _pr(db, approver)
        await _task(db, pr, "pr", approver)
        await db.commit()

    resp = await requester_client.post(f"/api/v1/pr/{pr.id}/remind")
    assert resp.status_code == 409
    assert "switched off company-wide" in resp.json()["detail"]
    await drain(5)
    assert captured_emails == []


async def test_reminder_refused_when_the_approver_muted_notifications(
    requester_client, captured_emails,
):
    """The dispatcher would silently skip this user. Saying "sent" would be a lie."""
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        approver = await _user(db)
        approver.notification_channel = "none"
        pr = await _pr(db, approver)
        await _task(db, pr, "pr", approver)
        await db.commit()

    resp = await requester_client.post(f"/api/v1/pr/{pr.id}/remind")
    assert resp.status_code == 409
    assert "cannot be reached" in resp.json()["detail"]
    await drain(5)
    assert approver.email not in _to(captured_emails)


async def test_reminder_refused_when_the_assignee_was_deactivated(
    requester_client, captured_emails,
):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        approver = await _user(db)
        approver.is_active = False
        pr = await _pr(db, approver)
        await _task(db, pr, "pr", approver)
        await db.commit()

    resp = await requester_client.post(f"/api/v1/pr/{pr.id}/remind")
    assert resp.status_code == 409
    await drain(5)
    assert approver.email not in _to(captured_emails)


async def test_reminder_on_a_missing_document_is_404(requester_client):
    resp = await requester_client.post(f"/api/v1/pr/{uuid.uuid4()}/remind")
    assert resp.status_code == 404


# ── Template ─────────────────────────────────────────────────────────────────

async def test_manual_reminder_template_is_seeded():
    """get_or_create backfills newly added keys, so no migration is needed —
    but only if the key is actually in the defaults."""
    async with session_module.AsyncSessionLocal() as db:
        cfg = await get_config(db)
        await db.commit()
    tpl = (cfg.email_templates or {}).get(MANUAL_REMINDER_TEMPLATE)
    assert tpl is not None
    assert "{sender_name}" in tpl["body"]
    assert "{link}" in tpl["body"]
