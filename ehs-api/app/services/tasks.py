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
DOC_COURSE = "ehs_crs"
DOC_JHSC = "ehs_jhsc"
DOC_DOCUMENT = "ehs_doc"
DOC_ASSET = "ehs_asset"
DOC_PERMIT = "ehs_ptw"
# A safety data sheet. No task hangs off one — the three-year review is
# chased through the deadline sweep — but the compliance calendar needs a
# path for it, and DEADLINE_DOC_TYPE used to name this one as a bare string
# while every other clock named a constant here. A test now asserts every
# deadline document type has a path in this map.
DOC_SDS = "ehs_sds"
# A scheduled round of a form — the thing that says "this inspection is due".
# ★ Was a bare string in two places and a path-map key in a third; named here
# for the same reason DOC_SDS is, after archiving a template left its pending
# rounds being chased with nothing to close them.
DOC_RUN = "ehs_run"

# Task types. None of these may start with "approve".
TASK_DO_ACTION = "ehs_do_action"
TASK_VERIFY_ACTION = "ehs_verify_action"
TASK_INVESTIGATE = "ehs_investigate"
TASK_STATUTORY = "ehs_statutory"
TASK_CERT_EXPIRY = "ehs_cert_expiry"
TASK_DO_FORM = "ehs_do_form"
TASK_TOOLBOX_TALK = "ehs_toolbox_talk"
# Running a course session's roster, versus being on it.
TASK_COURSE_ADMIN = "ehs_course_admin"
TASK_COURSE_ATTEND = "ehs_course_attend"
TASK_JHSC_REPLY = "ehs_jhsc_reply"
TASK_READ_SIGN = "ehs_read_sign"
TASK_ASSET_INSPECT = "ehs_asset_inspect"


# The role to label a task with when it is pinned to one person and the role is
# not what routes it. These read "whoever this individual employee is", and used
# to say "worker" until identity 0014 retired that role — everyone here is an
# employee, so a role meaning "an employee" told nobody anything. `requester` is
# the role every UniOps account actually holds.
ROLE_ANY_EMPLOYEE = "requester"
TASK_CLOSE_PERMIT = "ehs_close_permit"


# Where each kind of record is actually opened. A document type that is not
# here is still listed — it says what it is and who it is for — but without a
# link, which is better than a link that goes nowhere.
#
# ★ Lives here rather than beside the task endpoint because the compliance
# calendar needs the same answers. It had its own idea of where a row opens,
# which covered incidents and nothing else — and two maps of the same thing
# drift: "/jhsc?rec={id}" was corrected in one of them this round.
# ★ Where a task opens. The id goes in the path wherever the screen can use
# it: a task that lands on a register is a task that has told somebody to go
# and find their own work, which is the thing an inbox is for.
DOC_PATHS: dict[str, str] = {
    "ehs_tbt": "/toolbox-talks?delivery={id}",
    DOC_RUN: "/forms",
    "ehs_doc": "/documents/{id}",
    "ehs_crs": "/training",
    "ehs_cert": "/training",
    "ehs_asset": "/equipment",
    "ehs_inc": "/incidents/{id}",
    # ★ To the recommendation, now that the task hangs off one. It was "/jhsc" —
    # the committee's list page — so somebody told to write a statutory reply
    # landed on a list of meetings and had to find which meeting and which
    # recommendation it meant. Raised in review by uniops-9a: changing the
    # document without changing this would have left the link technically valid
    # and practically useless.
    "ehs_jhsc": "/jhsc?rec={id}",
    "ehs_ptw": "/permits/{id}",
    # ★ The id here is the data *sheet*, not the chemical — that is what a
    # "sds" deadline carries as its source_id (app/crud/chemical.py). The
    # page opens the product holding that sheet. It can match on the
    # current sheet because superseding a revision satisfies the old
    # sheet's clock, so an outstanding review is always on the current one.
    "ehs_sds": "/chemicals?sheet={id}",
}


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
    assigned_user_id: uuid.UUID | None = None,
    completed_by: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    """Close the open tasks for a document. Returns how many were closed.

    Used when the work a task was chasing is done through the application
    rather than by someone ticking the task off — closing an action closes its
    "do this" task, and nobody should have to do both.

    ★ Pass `assigned_user_id` whenever one document carries a task per person.
    Without it this closes everybody's: the second acceptance round reported
    that a published policy did not show up as something to do, and the reason
    was that the first person to sign it closed the read-and-sign task for the
    entire plant. The acknowledgement rows — the actual record — stayed
    correct, so the inbox and the compliance report disagreed, and the inbox
    was the one people were reading.

    `completed_by` is who is stamped on the rows, never a filter. Those are
    different questions and conflating them is what caused this.
    """
    stmt = select(Task).where(
        Task.document_type == doc_type,
        Task.document_id == doc_id,
        Task.is_completed.is_(False),
    )
    if task_type:
        stmt = stmt.where(Task.type == task_type)
    if assigned_user_id:
        stmt = stmt.where(Task.assigned_user_id == assigned_user_id)
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
