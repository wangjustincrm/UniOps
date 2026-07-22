"""Company default_channel behaves as a master switch for task notifications.

Regression: per-user ``notification_channel`` is NOT NULL (server default
'email_only'), so ``user.notification_channel or company_channel`` always fell
on the per-user value — setting the admin "Default Notification Channel" to
"none" did nothing and users kept getting email. default_channel='none' must
suppress notifications for everyone.
"""
import uuid

import pytest

import app.db.session as session_module
from app.crud import user as user_crud
from app.crud.config import get_or_create as get_config
from app.models.task import Task
from app.schemas.auth import RegisterRequest
from app.services import notification


async def _make_user(db):
    user = await user_crud.create(db, RegisterRequest(
        email=f"notif-{uuid.uuid4().hex[:8]}@example.com",
        password="TestPass1!",
        full_name="Notify Target",
        role="requester",
    ))
    await db.commit()
    # Default per-user channel is 'email_only' (the value that shadows the default).
    assert user.notification_channel == "email_only"
    return user


def _make_task(user) -> Task:
    return Task(
        type="approve_pr",
        document_type="pr",
        document_id=uuid.uuid4(),
        document_number="PR-TEST-1",
        assigned_role="requester",
        assigned_user_id=user.id,
        title="Approve PR-TEST-1",
    )


@pytest.fixture
def captured_emails(monkeypatch):
    sent: list[tuple] = []

    async def _fake_send_email(to, subject, html, **kwargs):
        sent.append((to, subject))

    # _dispatch imports send_email from app.services.email at call time.
    monkeypatch.setattr("app.services.email.send_email", _fake_send_email)
    return sent


async def _set_default_channel(db, value: str):
    cfg = await get_config(db)
    cfg.notification_settings = {**(cfg.notification_settings or {}), "default_channel": value}
    await db.commit()


async def test_default_channel_none_suppresses_all_email(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_default_channel(db, "none")
        user = await _make_user(db)
        await notification.dispatch_task_notification(_make_task(user), db)

    assert captured_emails == [], "default_channel=none must suppress email for every user"


async def test_default_channel_email_still_sends(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_default_channel(db, "email_only")
        user = await _make_user(db)
        await notification.dispatch_task_notification(_make_task(user), db)

    assert len(captured_emails) == 1


# ── Role shared mailbox ──────────────────────────────────────────────────────

from app.crud.config import role_display_name  # noqa: E402


async def test_role_display_name_builtin_custom_and_fallback():
    async with session_module.AsyncSessionLocal() as db:
        cfg = await get_config(db)
        cfg.custom_roles = [
            {"code": "ap_lead", "name": "AP Lead", "is_active": True},
            {"code": "ap_temp", "name": "AP Temp", "is_active": False},
        ]
        await db.commit()

        assert role_display_name(cfg, "ap_clerk") == "AP Clerk"
        assert role_display_name(cfg, "ap_lead") == "AP Lead"
        assert role_display_name(cfg, "ap_temp") == "Ap Temp"
        assert role_display_name(cfg, "some_new_role") == "Some New Role"
        assert role_display_name(cfg, None) == "Team"


async def test_default_notification_settings_has_shared_mailbox_map():
    from app.crud.config import _DEFAULT_NOTIFICATION_SETTINGS

    assert _DEFAULT_NOTIFICATION_SETTINGS["role_shared_mailboxes"] == {}


from sqlalchemy import select as sa_select  # noqa: E402

from app.models.notification_log import NotificationLog  # noqa: E402

SHARED_MAILBOX = "ap@canadaroyalmilk.com"


async def _make_user_with_role(db, role: str):
    user = await user_crud.create(db, RegisterRequest(
        email=f"notif-{uuid.uuid4().hex[:8]}@example.com",
        password="TestPass1!",
        full_name="AP Person",
        role=role,
    ))
    await db.commit()
    return user


async def _set_notif_settings(db, **kv):
    cfg = await get_config(db)
    cfg.notification_settings = {**(cfg.notification_settings or {}), **kv}
    await db.commit()


def _make_pool_task(role: str = "ap_clerk") -> Task:
    """Role-addressed task: no assigned_user_id."""
    return Task(
        type="create_pa",
        document_type="pa",
        document_id=uuid.uuid4(),
        document_number="PA-TEST-1",
        assigned_role=role,
        title="Process Payment: PA-TEST-1",
        description="Hi {recipient_name}, PA-TEST-1 needs processing.",
    )


@pytest.fixture
def captured_teams(monkeypatch):
    sent: list[tuple] = []

    async def _fake_send_teams_card(webhook, title, body, **kwargs):
        sent.append((webhook, title))

    monkeypatch.setattr("app.services.teams.send_teams_card", _fake_send_teams_card)
    return sent


async def test_shared_mailbox_replaces_per_member_email(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        for _ in range(3):
            await _make_user_with_role(db, "ap_clerk")
        await notification.dispatch_task_notification(_make_pool_task(), db)

    assert len(captured_emails) == 1, "role-pool task must produce exactly one email"
    assert captured_emails[0][0] == SHARED_MAILBOX


async def test_shared_mailbox_sends_even_when_role_pool_is_empty(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"vendor_manager": SHARED_MAILBOX},
        )
        await notification.dispatch_task_notification(_make_pool_task("vendor_manager"), db)

    assert len(captured_emails) == 1
    assert captured_emails[0][0] == SHARED_MAILBOX


async def test_without_shared_mailbox_every_member_is_emailed(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only", role_shared_mailboxes={})
        users = [await _make_user_with_role(db, "ap_clerk") for _ in range(3)]
        await notification.dispatch_task_notification(_make_pool_task(), db)

    sent_to = {to for to, _ in captured_emails}
    # Other tests may have left ap_clerk users behind, so assert containment,
    # not an exact count: every member is mailed and nothing goes to the mailbox.
    assert {u.email for u in users} <= sent_to
    assert SHARED_MAILBOX not in sent_to


async def test_named_assignee_ignores_shared_mailbox(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        user = await _make_user_with_role(db, "ap_clerk")
        task = _make_pool_task()
        task.assigned_user_id = user.id
        await notification.dispatch_task_notification(task, db)

    assert len(captured_emails) == 1
    assert captured_emails[0][0] == user.email


async def test_default_channel_none_beats_shared_mailbox(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="none",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        await _make_user_with_role(db, "ap_clerk")
        await notification.dispatch_task_notification(_make_pool_task(), db)

    assert captured_emails == []


async def test_shared_mailbox_skips_teams(captured_emails, captured_teams):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="both",
            teams_webhook_url="https://example.com/webhook",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        user = await _make_user_with_role(db, "ap_clerk")
        user.teams_account = "ap.person@example.com"
        await db.commit()
        await notification.dispatch_task_notification(_make_pool_task(), db)

    assert len(captured_emails) == 1
    assert captured_teams == [], "shared-mailbox delivery must not also post a Teams card"


async def test_shared_mailbox_body_greets_the_team(monkeypatch):
    bodies: list[str] = []

    async def _capture(to, subject, html, **kwargs):
        bodies.append(html)

    monkeypatch.setattr("app.services.email.send_email", _capture)

    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        await notification.dispatch_task_notification(_make_pool_task(), db)

    assert len(bodies) == 1
    assert "AP Clerk Team" in bodies[0]


async def test_shared_mailbox_delivery_is_logged_without_user(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        task = _make_pool_task()
        db.add(task)
        await db.flush()
        await notification.dispatch_task_notification(task, db)
        await db.commit()

        logs = (await db.execute(
            sa_select(NotificationLog).where(NotificationLog.task_id == task.id)
        )).scalars().all()

    assert len(logs) == 1
    assert logs[0].user_id is None
    assert logs[0].recipient_email == SHARED_MAILBOX
    assert logs[0].status == "ok"
