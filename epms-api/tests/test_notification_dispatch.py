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
