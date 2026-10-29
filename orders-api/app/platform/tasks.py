"""Problem tasks in the shared Task Inbox (design §3.5 / §13.13).

One open task per problem: the problem key `<prefix>:<event>:<entity>` is the
task's `document_number`; raising a problem that already has an open task
refreshes it instead of adding another, and `resolve_problem` completes it
when the condition clears. The prefix, document type and assignee come from
the business code.

Task writes run in a SAVEPOINT: a task that cannot be written is logged and
never fails the unit that raised it. Wiring errors (a key, type, document type
or title that cannot fit the shared columns) are raised, never swallowed.

One open task per problem also across concurrent units: the write takes a
transaction-scoped advisory lock on (document_type, problem key), so a second
unit raising the same problem waits and then refreshes the first one's task.

`notified_at` stays NULL so the Task Inbox notifier mails the task under its
policy — unless `notify()` says no: then the task is written as already
notified and is never mailed (dev and test stacks; product owner, 2026-10-28).
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, MetaData, Numeric, String, Table, Text, func, insert, select, update,
)
from sqlalchemy.dialects.postgresql import UUID

logger = logging.getLogger(__name__)

# Widths of the shared table's columns (epms-api app/models/task.py):
# document_number, type, document_type, title.
KEY_MAX, TYPE_MAX, DOCTYPE_MAX, TITLE_MAX = 40, 40, 10, 255

# Stable document_id per problem key (the shared table needs a UUID).
_NAMESPACE = uuid.UUID("6f1d2c1e-7a0b-4d55-9a49-3f1f6b0c2e11")


def shared_tasks_table(metadata: MetaData | None = None) -> Table:
    """The columns of the shared `tasks` table this service writes. Declared on
    its OWN MetaData by default, so it never reaches this service's alembic
    autogenerate — the table belongs to epms-api."""
    md = metadata or MetaData()
    return Table(
        "tasks", md,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("type", String(TYPE_MAX), nullable=False),
        Column("priority", String(10), nullable=False),
        Column("document_type", String(DOCTYPE_MAX), nullable=False),
        Column("document_id", UUID(as_uuid=True), nullable=False),
        Column("document_number", String(KEY_MAX), nullable=False),
        Column("assigned_role", String(50), nullable=False),
        Column("assigned_user_id", UUID(as_uuid=True), nullable=True),
        Column("title", String(TITLE_MAX), nullable=False),
        Column("description", Text, nullable=True),
        Column("due_date", Date, nullable=True),
        Column("amount", Numeric(15, 2), nullable=True),
        Column("vendor", String(255), nullable=True),
        Column("is_completed", Boolean, nullable=False),
        Column("completed_at", DateTime(timezone=True), nullable=True),
        Column("completed_by", UUID(as_uuid=True), nullable=True),
        Column("created_by", UUID(as_uuid=True), nullable=True),
        Column("notified_at", DateTime(timezone=True), nullable=True),
        Column("notify_vars", JSON, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )


@dataclass(frozen=True)
class ProblemTask:
    task_type: str
    title: str
    description: str | None = None
    priority: str = "normal"  # urgent | normal


Assignee = Callable[[object], Awaitable[uuid.UUID | None]]  # (session) → user id


class TaskService:
    def __init__(
        self,
        *,
        table: Table,
        prefix: str,
        document_type: str,
        assigned_role: str,
        assignee: Assignee,
        notify: Callable[[], bool] = lambda: True,
    ) -> None:
        if len(document_type) > DOCTYPE_MAX:
            raise ValueError(f"document_type {document_type!r} is longer than {DOCTYPE_MAX}")
        self.t = table
        self.prefix = prefix
        self.document_type = document_type
        self.assigned_role = assigned_role
        self.assignee = assignee
        self.notify = notify

    def problem_key(self, event: str, entity: object) -> str:
        key = f"{self.prefix}:{event}:{entity}"
        if len(key) > KEY_MAX:
            raise ValueError(f"problem key {key!r} is longer than {KEY_MAX} characters")
        return key

    def _open(self, key: str):
        t = self.t
        return (t.c.document_type == self.document_type) & (t.c.document_number == key) & (t.c.is_completed.is_(False))

    async def raise_problem(self, session, event: str, entity: object, task: ProblemTask) -> uuid.UUID | None:
        """Create the problem's task, or refresh the open one. Returns the task
        id, or None when the write failed (logged, never raised)."""
        key = self.problem_key(event, entity)
        if len(task.task_type) > TYPE_MAX:
            raise ValueError(f"task type {task.task_type!r} is longer than {TYPE_MAX}")
        if len(task.title) > TITLE_MAX:
            raise ValueError(f"task title is {len(task.title)} characters; the column holds {TITLE_MAX}")
        t, now = self.t, datetime.now(timezone.utc)
        try:
            async with session.begin_nested():
                await self._lock(session, key)
                existing = (await session.execute(
                    select(t.c.id).where(self._open(key)).order_by(t.c.created_at).limit(1).with_for_update()
                )).scalar_one_or_none()
                if existing is not None:
                    await session.execute(update(t).where(t.c.id == existing).values(
                        title=task.title, description=task.description, priority=task.priority, updated_at=now,
                    ))
                    return existing
                task_id = uuid.uuid4()
                await session.execute(insert(t).values(
                    id=task_id, type=task.task_type, priority=task.priority,
                    document_type=self.document_type, document_id=uuid.uuid5(_NAMESPACE, key),
                    document_number=key, assigned_role=self.assigned_role,
                    assigned_user_id=await self.assignee(session),
                    title=task.title, description=task.description,
                    is_completed=False, notified_at=None if self.notify() else now,
                    created_at=now, updated_at=now,
                ))
                return task_id
        except Exception:  # noqa: BLE001 — a task failure never fails the unit
            logger.exception("could not raise task %s", key)
            return None

    async def _lock(self, session, key: str) -> None:
        await session.execute(select(func.pg_advisory_xact_lock(
            func.hashtextextended(f"tasks:{self.document_type}:{key}", 0))))

    async def resolve_problem(self, session, event: str, entity: object, *, by: uuid.UUID | None = None) -> int:
        """Complete the problem's open task(s); returns how many were closed."""
        key = self.problem_key(event, entity)
        now = datetime.now(timezone.utc)
        try:
            async with session.begin_nested():
                res = await session.execute(update(self.t).where(self._open(key)).values(
                    is_completed=True, completed_at=now, completed_by=by, updated_at=now,
                ))
                return res.rowcount or 0
        except Exception:  # noqa: BLE001
            logger.exception("could not resolve task %s", key)
            return 0
