"""Writing Safety work into the shared UniOps task inbox.

Safety does not have an inbox of its own. It writes rows into the `tasks`
table that epms-api owns, which is what the Portal home page, the EPMS inbox
and the OA inbox all read. That is the established pattern — approval-api and
vms-api do the same — and it is the difference between assigning a corrective
action and the assignee ever finding out.

Two constraints are not negotiable:

`document_type` is at most twenty characters in production and ten in several
services' test databases, because four ORM declarations still say String(10)
after the column was widened. Safety's document types are all within ten.

A task `type` must not begin with "approve" unless the approval engine wrote
it. epms-api's POST /tasks/{id}/complete refuses to close such a task
(app/api/v1/tasks.py) — closing one outside the engine strands the document
mid-approval with no open approve task and no way to advance it, which is how
five purchase orders were left permanently stuck.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mirrors import Task

# doc_type values, all ten characters or fewer.
DOC_INCIDENT = "ehs_inc"
DOC_ACTION = "ehs_act"
DOC_CERT = "ehs_cert"

# Task types. None of these may start with "approve".
TASK_DO_ACTION = "ehs_do_action"
TASK_VERIFY_ACTION = "ehs_verify_action"
TASK_INVESTIGATE = "ehs_investigate"
TASK_STATUTORY = "ehs_statutory"
TASK_CERT_EXPIRY = "ehs_cert_expiry"


async def open_task(
    db: AsyncSession,
    *,
    task_type: str,
    doc_type: str,
    doc_id: uuid.UUID,
    doc_number: str,
    title: str,
    assigned_role: str,
    assigned_user_id: uuid.UUID | None = None,
    description: str | None = None,
    priority: str = "normal",
) -> Task:
    """Create an inbox task. Caller flushes or commits."""
    if task_type.startswith("approve"):
        raise ValueError(
            f"task type {task_type!r} would be mistaken for an approval task; "
            "epms-api refuses to let those be completed outside the engine"
        )
    task = Task(
        id=uuid.uuid4(),
        type=task_type,
        priority=priority,
        document_type=doc_type,
        document_id=doc_id,
        document_number=doc_number,
        assigned_role=assigned_role,
        assigned_user_id=assigned_user_id,
        title=title,
        description=description,
        is_completed=False,
    )
    db.add(task)
    return task


async def close_tasks_for(
    db: AsyncSession,
    *,
    doc_type: str,
    doc_id: uuid.UUID,
    task_type: str | None = None,
    completed_by: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    """Close the open tasks for a document. Returns how many were closed.

    Used when the work a task was chasing is done through the application
    rather than by someone ticking the task off — closing an action closes its
    "do this" task, and nobody should have to do both.
    """
    stmt = select(Task).where(
        Task.document_type == doc_type,
        Task.document_id == doc_id,
        Task.is_completed.is_(False),
    )
    if task_type:
        stmt = stmt.where(Task.type == task_type)
    tasks = list((await db.execute(stmt)).scalars())
    stamp = now or datetime.now(timezone.utc)
    for task in tasks:
        task.is_completed = True
        task.completed_at = stamp
        task.completed_by = completed_by
    return len(tasks)


async def open_tasks_for(db: AsyncSession, *, doc_type: str, doc_id: uuid.UUID) -> list[Task]:
    return list((await db.execute(
        select(Task).where(
            Task.document_type == doc_type,
            Task.document_id == doc_id,
            Task.is_completed.is_(False),
        )
    )).scalars())
