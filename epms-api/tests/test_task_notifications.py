"""Per-task-type notification policy: the outbox notifier, the daily digest and
the admin endpoints (Portal → Admin → Notifications).

Isolation: the tasks table and the company_config row are shared with every
other suite, so notification_settings is snapshotted/restored around each test,
sends are captured by monkeypatch (never SMTP), and assertions look only at
this test's own tasks / addresses — other suites leave un-notified rows behind
and the notifier will pick those up too.
"""
import copy
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update as sa_update

import app.db.session as session_module
from app.crud import user as user_crud
from app.crud.config import get_or_create as get_config
from app.models.config import CompanyConfig
from app.models.task import Task
from app.schemas.auth import RegisterRequest
from app.services.notification import queue_task_notification
from app.tasks import daily_followup, task_notifier


@pytest.fixture(autouse=True)
async def _restore_notification_settings():
    async with session_module.AsyncSessionLocal() as db:
        cfg = await get_config(db)
        snapshot = copy.deepcopy(cfg.notification_settings)
        await db.commit()
    yield
    async with session_module.AsyncSessionLocal() as db:
        await db.execute(
            sa_update(CompanyConfig).values(notification_settings=copy.deepcopy(snapshot))
        )
        await db.commit()


async def _set_ns(**kv) -> None:
    """Merge keys into notification_settings on EVERY config row (value None =
    drop) — duplicate rows from concurrent suites make one-row updates a coin flip."""
    async with session_module.AsyncSessionLocal() as db:
        cfg = await get_config(db)
        ns = {**(cfg.notification_settings or {})}
        for k, v in kv.items():
            if v is None:
                ns.pop(k, None)
            else:
                ns[k] = v
        await db.execute(sa_update(CompanyConfig).values(notification_settings=ns))
        await db.commit()


async def _user(channel: str = "email_only"):
    async with session_module.AsyncSessionLocal() as db:
        u = await user_crud.create(db, RegisterRequest(
            email=f"tn-{uuid.uuid4().hex[:10]}@example.com",
            password="TestPass1!", full_name="Tina Notify", role="requester",
        ))
        u.notification_channel = channel
        await db.commit()
        await db.refresh(u)
        return u


async def _task(type_: str, assignee=None, *, settled=True, completed=False,
                notified=False, created_ago: timedelta = timedelta(0)) -> Task:
    async with session_module.AsyncSessionLocal() as db:
        t = Task(
            type=type_, document_type="pr", document_id=uuid.uuid4(),
            document_number=f"TN-{uuid.uuid4().hex[:6].upper()}",
            assigned_role="requester",
            assigned_user_id=assignee.id if assignee else None,
            title=f"{type_} test", is_completed=completed,
            notified_at=datetime.now(timezone.utc) if notified else None,
        )
        db.add(t)
        await db.commit()
        if settled or created_ago:
            # Raw UPDATE: the ORM onupdate would put updated_at back to now.
            past = datetime.now(timezone.utc) - max(created_ago, timedelta(minutes=1))
            await db.execute(sa_update(Task).where(Task.id == t.id).values(
                updated_at=past if settled else datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc) - created_ago,
            ))
            await db.commit()
        await db.refresh(t)
        return t


async def _reload(task_id) -> Task:
    async with session_module.AsyncSessionLocal() as db:
        return await db.get(Task, task_id)


async def _drain() -> None:
    for _ in range(100):
        if await task_notifier.run_once() < task_notifier.BATCH:
            return


@pytest.fixture
def sent(monkeypatch):
    """Immediate sends made by the notifier, as (task_id, extra_vars)."""
    calls: list[tuple] = []

    async def _fake(task, db, **kwargs):
        calls.append((task.id, kwargs.get("extra_vars")))

    monkeypatch.setattr(task_notifier, "dispatch_task_notification", _fake)
    return calls


@pytest.fixture
async def started():
    """Pretend the one-time backlog adoption already happened."""
    await _set_ns(task_notifier_started_at="2026-10-08T00:00:00+00:00")


# ── Notifier ──────────────────────────────────────────────────────────────────

async def test_default_on_type_is_sent_and_stamped(sent, started):
    t = await _task("approve_pr")            # in DEFAULT_IMMEDIATE
    await _drain()
    assert t.id in [c[0] for c in sent]
    assert (await _reload(t.id)).notified_at is not None


async def test_default_off_type_is_stamped_without_sending(sent, started):
    t = await _task("approve_pa")            # not in DEFAULT_IMMEDIATE
    await _drain()
    assert t.id not in [c[0] for c in sent]
    assert (await _reload(t.id)).notified_at is not None


async def test_admin_turns_a_new_type_on(sent, started):
    await _set_ns(task_notifications={"approve_exp": {"email": True, "mode": "immediate"}})
    t = await _task("approve_exp")
    await _drain()
    assert t.id in [c[0] for c in sent]


async def test_admin_turns_a_default_type_off(sent, started):
    await _set_ns(task_notifications={"approve_pr": {"email": False, "mode": "immediate"}})
    t = await _task("approve_pr")
    await _drain()
    assert t.id not in [c[0] for c in sent]
    assert (await _reload(t.id)).notified_at is not None


async def test_digest_type_is_not_sent_immediately(sent, started):
    await _set_ns(task_notifications={"approve_pr": {"email": True, "mode": "digest"}})
    t = await _task("approve_pr")
    await _drain()
    assert t.id not in [c[0] for c in sent]
    assert (await _reload(t.id)).notified_at is not None


async def test_completed_before_pickup_is_not_sent(sent, started):
    t = await _task("approve_pr", completed=True)
    await _drain()
    assert t.id not in [c[0] for c in sent]


async def test_unsettled_row_waits(sent, started):
    """Picked up only once it has sat still for SETTLE — the creating request
    may still be adding notify_vars."""
    t = await _task("approve_pr", settled=False)
    await _drain()
    assert t.id not in [c[0] for c in sent]
    assert (await _reload(t.id)).notified_at is None


async def test_queue_renotifies_with_call_site_vars(sent, started):
    t = await _task("match_invoice", notified=True)
    async with session_module.AsyncSessionLocal() as db:
        row = await db.get(Task, t.id)
        queue_task_notification(row, extra_vars={"invoice_number": "INV-TN-1"})
        await db.commit()
        await db.execute(sa_update(Task).where(Task.id == t.id).values(
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()
    await _drain()
    assert (t.id, {"invoice_number": "INV-TN-1"}) in sent


async def test_first_start_adopts_backlog_without_sending(sent):
    """Rows the old containers left un-notified are stamped, never mailed."""
    await _set_ns(task_notifier_started_at=None)
    t = await _task("approve_pr")
    await task_notifier.run_once()
    assert t.id not in [c[0] for c in sent]
    assert (await _reload(t.id)).notified_at is not None
    async with session_module.AsyncSessionLocal() as db:
        assert (await get_config(db)).notification_settings.get("task_notifier_started_at")
    # …and only once: the next new task is handled normally.
    t2 = await _task("approve_pr")
    await _drain()
    assert t2.id in [c[0] for c in sent]


# ── Digest ────────────────────────────────────────────────────────────────────

@pytest.fixture
def mails(monkeypatch):
    out: list[tuple[str, str, str]] = []

    async def _fake_send(to, subject, html, **kw):
        out.append((to, subject, html))

    monkeypatch.setattr("app.services.email.send_email", _fake_send)
    return out


async def test_digest_bundles_one_email_per_person(mails):
    await _set_ns(task_notifications={
        "approve_pa": {"email": True, "mode": "digest"},
        "approve_agr": {"email": True, "mode": "digest"},
    })
    u = await _user()
    new = await _task("approve_pa", u, notified=True)
    old = await _task("approve_agr", u, notified=True, created_ago=timedelta(days=3))
    await daily_followup.run_daily_digest()
    mine = [m for m in mails if m[0] == u.email]
    assert len(mine) == 1
    body = mine[0][2]
    assert new.document_number in body and old.document_number in body
    assert body.count(">NEW<") == 1          # only the task from the last 24 h


async def test_digest_skips_immediate_and_off_types(mails):
    await _set_ns(task_notifications={"approve_pa": {"email": True, "mode": "digest"}})
    u = await _user()
    await _task("approve_pr", u, notified=True)     # immediate by default
    await _task("approve_agr", u, notified=True)    # off by default
    await daily_followup.run_daily_digest()
    assert [m for m in mails if m[0] == u.email] == []


async def test_digest_respects_personal_opt_out(mails):
    await _set_ns(task_notifications={"approve_pa": {"email": True, "mode": "digest"}})
    opted_out = await _user(channel="none")
    opted_in = await _user()
    await _task("approve_pa", opted_out, notified=True)
    await _task("approve_pa", opted_in, notified=True)
    await daily_followup.run_daily_digest()
    assert [m for m in mails if m[0] == opted_out.email] == []
    assert len([m for m in mails if m[0] == opted_in.email]) == 1


async def test_digest_escapes_task_text(mails):
    await _set_ns(task_notifications={"approve_pa": {"email": True, "mode": "digest"}})
    u = await _user()
    t = await _task("approve_pa", u, notified=True)
    async with session_module.AsyncSessionLocal() as db:
        await db.execute(sa_update(Task).where(Task.id == t.id).values(
            title='<img src=x onerror="alert(1)">'))
        await db.commit()
    await daily_followup.run_daily_digest()
    body = [m for m in mails if m[0] == u.email][0][2]
    assert "<img src=x" not in body and "&lt;img src=x" in body


# ── Admin endpoints ───────────────────────────────────────────────────────────

async def test_admin_lists_every_type_with_defaults(admin_client):
    r = await admin_client.get("/api/v1/config/task-notifications")
    assert r.status_code == 200
    rows = {p["task_type"]: p for p in r.json()["policies"]}
    # registry types, derived claim types, and their defaults
    assert rows["approve_pr"]["email"] is True and rows["approve_pr"]["default_email"] is True
    assert rows["approve_pa"]["email"] is False
    assert rows["approve_exp"]["module"] == "OA"
    assert "approve_mil" in rows and "revise_trv" in rows
    assert rows["chase_agreement_invoice"]["note"]


async def test_admin_saves_policy_and_generic_patch_cannot_undo_it(admin_client):
    stale = (await admin_client.get("/api/v1/config")).json()["notification_settings"]
    r = await admin_client.put("/api/v1/config/task-notifications", json={"policies": [
        {"task_type": "approve_pa", "email": True, "mode": "digest"},
    ]})
    assert r.status_code == 200
    rows = {p["task_type"]: p for p in r.json()["policies"]}
    assert rows["approve_pa"]["email"] is True and rows["approve_pa"]["mode"] == "digest"

    # A Portal form opened before that save posts back its stale copy.
    stale = {**(stale or {}), "task_notifications": {}}
    assert (await admin_client.patch("/api/v1/config", json={
        "notification_settings": stale})).status_code == 200
    rows = {p["task_type"]: p for p in
            (await admin_client.get("/api/v1/config/task-notifications")).json()["policies"]}
    assert rows["approve_pa"]["mode"] == "digest"


async def test_bad_mode_is_rejected(admin_client):
    r = await admin_client.put("/api/v1/config/task-notifications", json={"policies": [
        {"task_type": "approve_pa", "email": True, "mode": "hourly"},
    ]})
    assert r.status_code == 422


async def test_requester_cannot_read_or_change_policy(requester_client):
    assert (await requester_client.get("/api/v1/config/task-notifications")).status_code == 403
    assert (await requester_client.put("/api/v1/config/task-notifications",
                                       json={"policies": []})).status_code == 403
