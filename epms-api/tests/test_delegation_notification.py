"""Task 8: mail the delegate instead of the person on leave.

This is the ONE place in the delegation feature that SUBSTITUTES the
recipient rather than widening it: everywhere else delegation adds the
delegate alongside the delegator, but a person on leave should not be pinged
with approval-reminder email — that is noise, not help. Only pinned
(``assigned_user_id`` set) APPROVE tasks are affected:

  1. Inside an active delegation window, a pinned approve task addressed to
     the delegator mails the DELEGATE, and never the delegator.
  2. Outside the window (delegation not yet started / already ended), it
     mails the delegator as usual.
  3. If the delegate's account is deactivated, the substitution must not
     happen — the mail falls back to the delegator, never vanishing into a
     disabled account.
  4. A NON-approval task addressed to the delegator (task.type does not start
     with "approve") still mails the delegator even inside an active window
     — substitution is approval-task-only.

The shared-mailbox / role-pool path (``assigned_user_id IS NULL``) is
untouched by this feature entirely and is already covered by
test_notification_dispatch.py.
"""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

import app.db.session as session_module
from app.core.delegation import local_today
from app.crud import user as user_crud
from app.crud.config import get_or_create as get_config
from app.models.task import Task
from app.schemas.auth import RegisterRequest
from app.services import notification

TAG = uuid.uuid4().hex[:8]


async def _set_notif_settings(db, **kv):
    """Merge keys into company_config.notification_settings on every row.

    Mirrors test_notification_dispatch.py's helper: company_config has no
    singleton constraint and get_or_create's SELECT has no ORDER BY, so
    writing every row makes the setting unambiguous regardless of which row
    the dispatcher's own get_or_create lands on.
    """
    from sqlalchemy import update as sa_update
    from app.models.config import CompanyConfig

    cfg = await get_config(db)
    merged = {**(cfg.notification_settings or {}), **kv}
    await db.execute(sa_update(CompanyConfig).values(notification_settings=merged))
    await db.commit()


@pytest.fixture
def captured_emails(monkeypatch):
    sent: list[tuple] = []

    async def _fake_send_email(to, subject, html, **kwargs):
        sent.append((to, subject))

    # _dispatch imports send_email from app.services.email at call time.
    monkeypatch.setattr("app.services.email.send_email", _fake_send_email)
    return sent


def _to(captured) -> list[str]:
    return [to for to, _ in captured]


async def _make_user(db, *, is_active=True, full_name="Delegation Person"):
    user = await user_crud.create(db, RegisterRequest(
        email=f"deleg-{uuid.uuid4().hex[:8]}-{TAG}@example.com",
        password="TestPass1!",
        full_name=full_name,
        role="dept_manager",
    ))
    if not is_active:
        user.is_active = False
    await db.commit()
    return user


async def _insert_delegation(db, *, delegator_id, delegate_id, start_date, end_date):
    await db.execute(text(
        "INSERT INTO approval_delegations "
        "(id, delegator_user_id, delegate_user_id, start_date, end_date, "
        " revoked_at, created_by) "
        "VALUES (:id, :delegator_id, :delegate_id, :start_date, :end_date, "
        " NULL, :created_by)"),
        {
            "id": uuid.uuid4(), "delegator_id": delegator_id,
            "delegate_id": delegate_id, "start_date": start_date,
            "end_date": end_date, "created_by": uuid.uuid4(),
        })
    await db.commit()


def _make_pinned_task(user, *, type_="approve_pr") -> Task:
    """A task pinned to a specific person (assigned_user_id set) — the only
    shape substitution applies to."""
    return Task(
        type=type_,
        document_type="pr",
        document_id=uuid.uuid4(),
        document_number=f"PR-DELEG-{uuid.uuid4().hex[:6]}",
        assigned_role="dept_manager",
        assigned_user_id=user.id,
        title="Approve PR-DELEG",
    )


async def test_pinned_approve_task_inside_window_mails_the_delegate(captured_emails):
    today = local_today()
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        delegator = await _make_user(db, full_name="On Leave")
        delegate = await _make_user(db, full_name="Stand In")
        await _insert_delegation(
            db, delegator_id=delegator.id, delegate_id=delegate.id,
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=3),
        )
        task = _make_pinned_task(delegator, type_="approve_pr")
        await notification.dispatch_task_notification(task, db)

    sent_to = _to(captured_emails)
    assert delegate.email in sent_to, "the stand-in must receive the approve-task email"
    assert delegator.email not in sent_to, \
        "the person on leave must NOT be mailed — substitution, not widening"


async def test_pinned_approve_task_outside_window_mails_the_delegator(captured_emails):
    today = local_today()
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        delegator = await _make_user(db, full_name="Not On Leave Yet")
        delegate = await _make_user(db, full_name="Future Stand In")
        # Window has not started yet.
        await _insert_delegation(
            db, delegator_id=delegator.id, delegate_id=delegate.id,
            start_date=today + timedelta(days=1), end_date=today + timedelta(days=5),
        )
        task = _make_pinned_task(delegator, type_="approve_pr")
        await notification.dispatch_task_notification(task, db)

    sent_to = _to(captured_emails)
    assert delegator.email in sent_to
    assert delegate.email not in sent_to, \
        "outside the delegation window, the delegate must not be mailed"


async def test_deactivated_delegate_falls_back_to_delegator(captured_emails):
    today = local_today()
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        delegator = await _make_user(db, full_name="Still Working")
        delegate = await _make_user(db, is_active=False, full_name="Deactivated Standin")
        await _insert_delegation(
            db, delegator_id=delegator.id, delegate_id=delegate.id,
            start_date=today, end_date=today + timedelta(days=3),
        )
        task = _make_pinned_task(delegator, type_="approve_pr")
        await notification.dispatch_task_notification(task, db)

    sent_to = _to(captured_emails)
    assert delegator.email in sent_to, \
        "a deactivated stand-in must not swallow the mail — fall back to the delegator"
    assert delegate.email not in sent_to


async def test_non_approval_task_still_mails_the_delegator(captured_emails):
    """Substitution is scoped to task.type.startswith('approve'). A pinned
    non-approval task (e.g. confirm_receipt) addressed to the delegator must
    still reach the delegator even inside an active delegation window."""
    today = local_today()
    async with session_module.AsyncSessionLocal() as db:
        await _set_notif_settings(db, default_channel="email_only")
        delegator = await _make_user(db, full_name="On Leave But Not Approving")
        delegate = await _make_user(db, full_name="Stand In Not Involved")
        await _insert_delegation(
            db, delegator_id=delegator.id, delegate_id=delegate.id,
            start_date=today, end_date=today + timedelta(days=3),
        )
        task = _make_pinned_task(delegator, type_="confirm_receipt")
        await notification.dispatch_task_notification(task, db)

    sent_to = _to(captured_emails)
    assert delegator.email in sent_to, \
        "a non-approval task must still mail the delegator, unaffected by delegation"
    assert delegate.email not in sent_to
