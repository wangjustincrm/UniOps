"""Creating the inspections that are due, and chasing the ones that are late.

Two passes, both idempotent — the sweep runs every few minutes and must not
produce a second copy of anything it already made:

* **generate** — for each active schedule, create a run for every occurrence
  now inside its lead time, advance the rotation, and open a task. The unique
  constraint on (schedule, due date) is what makes a restart mid-sweep safe;
  the code checks first so the common path does not rely on catching an error.

* **chase** — mark anything past its due date as missed and notify. A run is
  marked once: `status` moves pending → missed, and the pass acts on the
  change rather than on the condition, so a schedule left overdue for a week
  does not send seven emails.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.form import FormTemplate
from app.models.mirrors import Department
from app.models.schedule import FormSchedule, FormScheduleRun
from app.services import tasks as task_service
from app.services.recurrence import due_occurrences, next_due, rotation_pick

logger = logging.getLogger(__name__)

# Task type lives with the others in tasks.py; doc_type is ours and must
# stay within tasks.document_type (varchar 20).
TASK_DO_FORM = task_service.TASK_DO_FORM
DOC_FORM_RUN = "ehs_run"


async def _department_name(db: AsyncSession, dept_id: uuid.UUID | None) -> str | None:
    if dept_id is None:
        return None
    return (await db.execute(
        select(Department.name).where(Department.id == dept_id))).scalar()


async def generate_due(db: AsyncSession, *, today: date | None = None) -> list[FormScheduleRun]:
    """Create the occurrences that should exist by now."""
    today = today or datetime.now(timezone.utc).date()
    created: list[FormScheduleRun] = []

    schedules = (await db.execute(
        select(FormSchedule).where(FormSchedule.is_active.is_(True))
    )).scalars().all()

    for sched in schedules:
        occurrences = due_occurrences(
            next_due_on=sched.next_due_on, frequency=sched.frequency,
            today=today, lead_days=sched.lead_days,
        )
        if not occurrences:
            continue

        template = await db.get(FormTemplate, sched.template_id)
        if template is None or not template.is_active:
            # A retired template stops generating work rather than producing
            # tasks pointing at a form nobody can fill in.
            continue

        for due_on in occurrences:
            existing = (await db.execute(
                select(FormScheduleRun).where(
                    FormScheduleRun.schedule_id == sched.id,
                    FormScheduleRun.due_on == due_on,
                )
            )).scalar_one_or_none()
            if existing is not None:
                continue

            dept_id = None
            if sched.rotation == "department":
                picked = rotation_pick(sched.department_ids, sched.rotation_index)
                dept_id = uuid.UUID(picked) if picked else None
                # Advance whether or not anyone fills it in: the rotation is
                # "next department's turn", not "next department that complied".
                sched.rotation_index += 1

            run = FormScheduleRun(
                id=uuid.uuid4(), schedule_id=sched.id, template_id=sched.template_id,
                due_on=due_on, department_id=dept_id,
                department_name=await _department_name(db, dept_id),
                location_id=uuid.UUID(sched.location_ids[0]) if sched.location_ids else None,
            )
            db.add(run)
            await db.flush()
            created.append(run)

            where = f" — {run.department_name}" if run.department_name else ""
            await task_service.open_task(
                db,
                task_type=TASK_DO_FORM,
                doc_type=DOC_FORM_RUN,
                doc_id=run.id,
                doc_number=f"{template.code}/{due_on.isoformat()}",
                title=f"{template.name}{where}",
                assigned_role=sched.assigned_role or "ehs_coordinator",
                assigned_user_id=sched.assigned_user_id,
                description=f"Due {due_on.isoformat()}",
                priority="normal",
            )

            sched.next_due_on = next_due(due_on, sched.frequency)
            sched.last_generated_on = today

    await db.flush()
    return created


async def chase_overdue(db: AsyncSession, *, today: date | None = None) -> list[FormScheduleRun]:
    """Mark anything past due as missed. Acts on the change, so it notifies once."""
    today = today or datetime.now(timezone.utc).date()

    overdue = (await db.execute(
        select(FormScheduleRun).where(
            FormScheduleRun.status == "pending",
            FormScheduleRun.due_on < today,
        )
    )).scalars().all()

    for run in overdue:
        run.status = "missed"

    await db.flush()
    return list(overdue)


async def complete_run_for_submission(
    db: AsyncSession, submission_id: uuid.UUID
) -> FormScheduleRun | None:
    """Close the run a submitted form belongs to, if it came from a schedule."""
    run = (await db.execute(
        select(FormScheduleRun).where(FormScheduleRun.submission_id == submission_id)
    )).scalar_one_or_none()
    if run is None or run.status == "completed":
        return run

    run.status = "completed"
    run.completed_at = datetime.now(timezone.utc)
    await task_service.close_tasks_for(db, doc_type=DOC_FORM_RUN, doc_id=run.id)
    await db.flush()
    return run
