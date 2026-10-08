"""Manual "Send reminder" — nudge whoever is holding a document's open approval task.

The Approval Timeline on the PR / PO detail pages renders a "Send reminder" link
under the step that is currently waiting. Until this module existed the link only
called `console.log`: no request, no email, and — worst of all — no feedback, so
a user who clicked it believed a reminder had gone out.

Design notes:

* **Recipients are never taken from the client.** The button knows a workflow
  node id, but the authority on "who is actually waiting" is the `tasks` table —
  the same source the Task Inbox and every other notification path uses. So the
  endpoint resolves the open approval task itself and hands it to the ordinary
  dispatcher.
* **The cooldown lives in `notification_logs`, not a new column** — the same
  zero-migration trick `app/tasks/service_gr_due.py` uses for its once-only
  escalation. The marker row is written *synchronously*, before the background
  dispatch is spawned, so a double click cannot slip a second email through the
  window between "check the log" and "the dispatcher writes the log".
* **Unreachable is reported, not swallowed.** Notifications off company-wide, an
  approver who set their channel to "none", a task whose assignee was
  deactivated — each of those makes the nudge a no-op, and the clicker is told
  so instead of getting a green "sent".
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_log import NotificationLog
from app.models.task import Task
from app.services.notification import (
    fire_and_forget_notify,
    resolve_task_recipients,
)

# Template key (seeded in app/crud/config.py, editable in EPMS Admin → Email
# Templates). Also the marker key this module reads back for the cooldown, so
# renaming it resets every in-flight cooldown — which is harmless but noisy.
MANUAL_REMINDER_TEMPLATE = "manual_reminder"

COOLDOWN_HOURS = 24

# Which open task means "someone is waiting to approve this". Deliberately NOT
# "any open task on the document": a PO can carry a place_order or a create_pa
# task long after approval is done, and nudging those holders from a link that
# sits under the *approval* timeline would mail the wrong person.
APPROVAL_TASK_TYPES: dict[str, tuple[str, ...]] = {
    "pr": ("approve_pr",),
    "po": ("approve_po",),
}


class ReminderUnavailable(Exception):
    """Nothing to remind about, or nobody the reminder could reach (→ 409)."""


class ReminderCooldown(Exception):
    """A reminder for this task went out recently (→ 429)."""

    def __init__(self, retry_at: datetime):
        self.retry_at = retry_at
        super().__init__(
            f"A reminder was already sent for this step in the last {COOLDOWN_HOURS} "
            f"hours. You can send another one after "
            f"{retry_at.strftime('%Y-%m-%d %H:%M')} UTC."
        )


@dataclass
class ReminderResult:
    recipients: list[str]
    document_number: str
    next_allowed_at: datetime
    # The tasks whose cooldown markers were just staged. The endpoint hands
    # these back to dispatch_manual_reminder() once its commit has landed.
    tasks: list[Task]


async def _open_approval_tasks(
    db: AsyncSession, document_type: str, document_id: uuid.UUID,
) -> list[Task]:
    result = await db.execute(
        select(Task).where(
            Task.document_type == document_type,
            Task.document_id == document_id,
            Task.is_completed.is_(False),
            Task.type.in_(APPROVAL_TASK_TYPES[document_type]),
        )
    )
    return list(result.scalars().all())


async def _last_reminder_at(db: AsyncSession, task_ids: list[uuid.UUID]) -> datetime | None:
    """When a manual reminder last went out for any of these tasks.

    Counts both the synchronous 'queued' marker this module writes and the 'ok'
    rows the dispatcher writes per recipient: either one proves a reminder was
    already issued inside the window.
    """
    return (await db.execute(
        select(func.max(NotificationLog.sent_at)).where(
            NotificationLog.task_id.in_(task_ids),
            NotificationLog.template_key == MANUAL_REMINDER_TEMPLATE,
            NotificationLog.status.in_(("queued", "ok")),
        )
    )).scalar_one_or_none()


def _days_waiting(task: Task) -> int:
    created = task.created_at
    if created is None:
        return 0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - created).days)


async def send_manual_reminder(
    db: AsyncSession,
    *,
    document_type: str,
    document_id: uuid.UUID,
) -> ReminderResult:
    """Send a reminder to the current approver(s). Raises on every no-op path.

    The caller must commit: the cooldown marker rows are added to `db` here but
    the background dispatch is only spawned after the commit, by the endpoint.
    """
    tasks = await _open_approval_tasks(db, document_type, document_id)
    if not tasks:
        raise ReminderUnavailable(
            "This document is not waiting on an approver right now, so there is "
            "nobody to remind."
        )

    from app.crud.config import get_or_create as get_config, role_display_name

    cfg = await get_config(db)
    notif_settings: dict = cfg.notification_settings or {}
    company_channel: str = notif_settings.get("default_channel", "email_only")
    if company_channel == "none":
        raise ReminderUnavailable(
            "Notifications are switched off company-wide (Admin → Notification "
            "Settings), so no reminder can be sent."
        )

    now = datetime.now(timezone.utc)
    last = await _last_reminder_at(db, [t.id for t in tasks])
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        retry_at = last + timedelta(hours=COOLDOWN_HOURS)
        if retry_at > now:
            raise ReminderCooldown(retry_at)

    # Who would actually receive it — resolved with the dispatcher's own rules
    # (delegation substitution, role pool ∪ additional roles, shared mailbox),
    # so the confirmation the clicker reads is not a second, drifting guess.
    labels: list[str] = []
    for task in tasks:
        resolved = await resolve_task_recipients(db, task, notif_settings)
        if resolved.shared_mailbox:
            if company_channel != "teams_only":
                labels.append(
                    f"{role_display_name(cfg, task.assigned_role)} "
                    f"({resolved.shared_mailbox})"
                )
            continue
        for user in resolved.users:
            if (user.notification_channel or company_channel) == "none":
                continue
            labels.append(user.full_name)

    if not labels:
        raise ReminderUnavailable(
            "The pending approver cannot be reached — either the task has no "
            "active assignee, or everyone it is addressed to has turned "
            "notifications off in their profile."
        )

    for task in tasks:
        # Written before the dispatch is spawned: this row alone starts the
        # cooldown, so a second click during the send cannot double-mail.
        db.add(NotificationLog(
            task_id=task.id,
            channel="email",
            template_key=MANUAL_REMINDER_TEMPLATE,
            status="queued",
        ))

    return ReminderResult(
        recipients=labels,
        document_number=tasks[0].document_number,
        next_allowed_at=now + timedelta(hours=COOLDOWN_HOURS),
        tasks=tasks,
    )


def dispatch_manual_reminder(db: AsyncSession, tasks: list[Task], sender_name: str) -> None:
    """Spawn the actual sends. Call AFTER the commit that persists the markers."""
    for task in tasks:
        fire_and_forget_notify(
            task, db,
            template_key=MANUAL_REMINDER_TEMPLATE,
            extra_vars={
                "sender_name": sender_name,
                "days_waiting": _days_waiting(task),
            },
        )


async def remind_document(
    db: AsyncSession,
    *,
    document_type: str,
    document_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> "ReminderResponse":
    """Endpoint-level wrapper: resolve → stage marker → commit → dispatch.

    The commit has to happen here rather than in the router: the background
    dispatch opens its own session and re-reads the task, so it must not be
    spawned until this session's marker row is visible to it.
    """
    from fastapi import HTTPException

    from app.models.user import User
    from app.schemas.reminder import ReminderResponse

    try:
        result = await send_manual_reminder(
            db, document_type=document_type, document_id=document_id,
        )
    except ReminderCooldown as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except ReminderUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    actor = await db.get(User, actor_id)
    sender_name = actor.full_name if actor else "A colleague"

    await db.commit()
    dispatch_manual_reminder(db, result.tasks, sender_name)

    return ReminderResponse(
        sent=True,
        document_number=result.document_number,
        recipients=result.recipients,
        next_allowed_at=result.next_allowed_at,
    )
