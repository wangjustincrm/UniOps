"""Company default_channel behaves as a master switch for task notifications.

Regression: per-user ``notification_channel`` is NOT NULL (server default
'email_only'), so ``user.notification_channel or company_channel`` always fell
on the per-user value — setting the admin "Default Notification Channel" to
"none" did nothing and users kept getting email. default_channel='none' must
suppress notifications for everyone.

Also covers the per-role shared mailbox: role-addressed tasks whose role has a
configured shared mailbox get exactly one email to that mailbox instead of a
per-member fan-out.

Test-isolation note: ``captured_emails`` collects EVERY email dispatched while
its monkeypatch is live — including strays from background tasks started by
other test files — and the ``company_config`` row plus the ``users`` table are
not reset between tests. Every test here therefore filters the capture list
down to its own mail (by recipient address) instead of asserting on the global
length, and ``_restore_notification_settings`` puts the config row back after
each test.
"""
import copy
import uuid

import pytest
from sqlalchemy import update as sa_update

import app.db.session as session_module
from app.crud import user as user_crud
from app.crud.config import get_or_create as get_config
from app.models.config import CompanyConfig
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


@pytest.fixture(autouse=True)
async def _restore_notification_settings():
    """快照/还原 company_config.notification_settings。

    ``get_or_create`` returns the one shared config row, and the helpers below
    merge into its ``notification_settings`` JSONB and commit. Without a
    restore, whatever the last test in this file set (shared mailboxes, a Teams
    webhook, default_channel) would leak into every later test file in the same
    session. Autouse so the two pre-existing default_channel tests are covered
    as well.
    """
    async with session_module.AsyncSessionLocal() as db:
        cfg = await get_config(db)
        snapshot = copy.deepcopy(cfg.notification_settings)
        await db.commit()
    yield
    async with session_module.AsyncSessionLocal() as db:
        await _write_notif_settings(db, copy.deepcopy(snapshot))


async def _write_notif_settings(db, value: dict | None) -> None:
    """Write notification_settings to EVERY company_config row, then commit.

    ``company_config`` has no singleton constraint and ``crud.config.get_or_create``
    is a racy ``SELECT ... LIMIT 1`` with no ORDER BY, so earlier suites that hit
    the API concurrently leave several duplicate rows behind (measured: 4 rows
    after ``test_invoice_assign.py``). Updating only "the" row we read is then a
    coin flip — the dispatcher's own ``get_or_create`` can pick a different row
    and read stale defaults. Writing all rows makes the setting unambiguous no
    matter which one the dispatcher lands on.
    """
    await db.execute(sa_update(CompanyConfig).values(notification_settings=value))
    await db.commit()


async def _set_notif_settings(db, **kv):
    """Merge keys into company_config.notification_settings (e.g.
    ``default_channel=...``, ``role_shared_mailboxes=...``)."""
    cfg = await get_config(db)
    await _write_notif_settings(db, {**(cfg.notification_settings or {}), **kv})


def _to(captured) -> list[str]:
    return [to for to, _ in captured]


async def test_default_channel_none_suppresses_all_email(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="none")
        user = await _make_user(db)
        await notification.dispatch_task_notification(_make_task(user), db)

    assert user.email not in _to(captured_emails), \
        "default_channel=none must suppress email for every user"


async def test_default_channel_email_still_sends(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        user = await _make_user(db)
        await notification.dispatch_task_notification(_make_task(user), db)

    assert _to(captured_emails).count(user.email) == 1


async def test_requester_task_without_assignee_never_broadcasts(captured_emails):
    """'requester' is not a role pool. A requester-addressed task with no concrete
    assignee means the document lost its PR link (imported PO) — fanning out
    would email every requester in the company (2026-08-05: a GR ack on a
    PMS-imported PO mailed 59 people). Suppress the fan-out and alert admins."""
    async with session_module.AsyncSessionLocal() as db:
        requester = await _make_user(db)                      # role=requester
        admin = await _make_user_with_role(db, "system_admin", "Alert Admin")
        task = _make_task(requester)
        task.assigned_user_id = None                          # broadcast shape
        db.add(task)
        await db.commit()
        await notification.dispatch_task_notification(task, db)
        await db.commit()

    assert requester.email not in _to(captured_emails), \
        "requester-role broadcast must be suppressed"
    assert admin.email in _to(captured_emails), \
        "admins must be alerted when a requester task has no assignee"


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


async def _make_user_with_role(db, role: str, full_name: str = "AP Person"):
    user = await user_crud.create(db, RegisterRequest(
        email=f"notif-{uuid.uuid4().hex[:8]}@example.com",
        password="TestPass1!",
        full_name=full_name,
        role=role,
    ))
    await db.commit()
    return user


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
        members = [await _make_user_with_role(db, "ap_clerk") for _ in range(3)]
        await notification.dispatch_task_notification(_make_pool_task(), db)

    sent_to = _to(captured_emails)
    assert sent_to.count(SHARED_MAILBOX) == 1, "role-pool task must produce exactly one shared-mailbox email"
    assert not ({u.email for u in members} & set(sent_to)), \
        "shared mailbox must replace the per-member fan-out, not add to it"


async def test_shared_mailbox_sends_even_when_role_pool_is_empty(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"vendor_manager": SHARED_MAILBOX},
        )
        await notification.dispatch_task_notification(_make_pool_task("vendor_manager"), db)

    assert _to(captured_emails).count(SHARED_MAILBOX) == 1


async def test_without_shared_mailbox_every_member_is_emailed(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only", role_shared_mailboxes={})
        users = [await _make_user_with_role(db, "ap_clerk") for _ in range(3)]
        await notification.dispatch_task_notification(_make_pool_task(), db)

    sent_to = set(_to(captured_emails))
    # Other tests may have left ap_clerk users behind, so assert containment,
    # not an exact count: every member is mailed and nothing goes to the mailbox.
    assert {u.email for u in users} <= sent_to
    assert SHARED_MAILBOX not in sent_to


async def test_broadcast_email_reaches_secondary_role_holder(captured_emails):
    """A broadcast task (e.g. the now-broadcast gm/opm step) must email every
    holder of the role — including a user who holds it as a SECONDARY role via
    identity's user_roles, not as their primary users.role. The Task Inbox
    already shows the task to such a holder (get_for_role unions user_roles);
    the email fan-out must reach the same set, or a GM/OPM who holds the post as
    an additional role (real case: sivers = Department Manager + OPM) sees the
    task in-app but never gets the email."""
    from sqlalchemy import text

    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only", role_shared_mailboxes={})
        # Primary role is dept_manager; OPM is held as an ADDITIONAL role.
        holder = await _make_user_with_role(db, "dept_manager", full_name="Sivers Holder")
        await db.execute(
            text("INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'opm')"),
            {"u": str(holder.id)},
        )
        await db.commit()
        await notification.dispatch_task_notification(_make_pool_task("opm"), db)

    assert holder.email in _to(captured_emails), \
        "a secondary-role OPM holder must receive the broadcast approval email"


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

    sent_to = _to(captured_emails)
    assert sent_to.count(user.email) == 1
    assert SHARED_MAILBOX not in sent_to, \
        "a named assignee must be mailed directly, never via the role mailbox"


async def test_default_channel_none_beats_shared_mailbox(captured_emails):
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="none",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        member = await _make_user_with_role(db, "ap_clerk")
        await notification.dispatch_task_notification(_make_pool_task(), db)

    sent_to = _to(captured_emails)
    assert SHARED_MAILBOX not in sent_to
    assert member.email not in sent_to


async def test_teams_only_channel_sends_nothing_for_shared_mailbox(captured_emails, captured_teams):
    """default_channel=teams_only + a configured shared mailbox → nothing at all.

    The shared-mailbox path deliberately never posts a Teams card, so when the
    company channel excludes email there is nothing left to dispatch — and it
    must NOT silently fall back to the per-member fan-out either.
    """
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="teams_only",
            teams_webhook_url="https://example.com/webhook",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        member = await _make_user_with_role(db, "ap_clerk")
        member.notification_channel = "teams_only"
        member.teams_account = "ap.person@example.com"
        await db.commit()
        await notification.dispatch_task_notification(_make_pool_task(), db)

    sent_to = _to(captured_emails)
    assert SHARED_MAILBOX not in sent_to, \
        "teams_only must not send the shared-mailbox email"
    assert member.email not in sent_to, \
        "a configured shared mailbox must not fall back to the per-member fan-out"
    assert captured_teams == [], "the shared-mailbox path never posts a Teams card"


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

    assert _to(captured_emails).count(SHARED_MAILBOX) == 1
    assert captured_teams == [], "shared-mailbox delivery must not also post a Teams card"


async def test_shared_mailbox_body_greets_the_team(monkeypatch):
    bodies: list[tuple[str, str]] = []

    async def _capture(to, subject, html, **kwargs):
        bodies.append((to, html))

    monkeypatch.setattr("app.services.email.send_email", _capture)

    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        await notification.dispatch_task_notification(_make_pool_task(), db)

    mine = [html for to, html in bodies if to == SHARED_MAILBOX]
    assert len(mine) == 1
    assert "AP Clerk Team" in mine[0]


async def test_shared_mailbox_body_renders_placeholders_without_template(monkeypatch):
    """Shared-mailbox delivery with no matching template still substitutes vars.

    Covers the ``else`` branch (falsy ``tpl``) of the shared-mailbox path: the
    body falls back to task.description and must render {recipient_name} as the
    role's team name, not emit the literal placeholder.
    """
    bodies: list[tuple[str, str]] = []

    async def _capture(to, subject, html, **kwargs):
        bodies.append((to, html))

    monkeypatch.setattr("app.services.email.send_email", _capture)

    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(
            db,
            default_channel="email_only",
            role_shared_mailboxes={"ap_clerk": SHARED_MAILBOX},
        )
        # template_key with no entry in email_templates → the no-template branch.
        await notification.dispatch_task_notification(
            _make_pool_task(), db, template_key="no_such_template_key",
        )

    mine = [html for to, html in bodies if to == SHARED_MAILBOX]
    assert len(mine) == 1
    assert "Hi AP Clerk Team," in mine[0]
    assert "{recipient_name}" not in mine[0]


async def test_named_assignee_body_renders_placeholders_without_template(monkeypatch):
    """Template-less per-user delivery must render {recipient_name} too.

    Mirrors the shared-mailbox branch: with no template for the task type, the
    body still comes from task.description and must have its placeholders
    substituted, not emit a literal "{recipient_name}".
    """
    bodies: list[tuple[str, str]] = []

    async def _capture(to, subject, html, **kwargs):
        bodies.append((to, html))

    monkeypatch.setattr("app.services.email.send_email", _capture)

    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only", role_shared_mailboxes={})
        user = await _make_user_with_role(db, "ap_clerk", full_name="Norbert Placeholder")
        task = _make_pool_task()
        task.assigned_user_id = user.id
        # template_key with no entry in email_templates → the no-template branch.
        await notification.dispatch_task_notification(
            task, db, template_key="no_such_template_key",
        )

    mine = [html for to, html in bodies if to == user.email]
    assert len(mine) == 1
    assert "Hi Norbert," in mine[0]
    assert "{recipient_name}" not in mine[0]


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


def test_confirm_receipt_template_and_link():
    from app.services.notification import _infer_template, _task_link
    import uuid
    pid = uuid.uuid4()
    assert _infer_template("confirm_receipt", is_followup=False) == "confirm_receipt"
    link = _task_link("po", pid, task_type="confirm_receipt")
    assert link.endswith(f"/gr/new?poId={pid}")
