"""The loop that chases deadlines.

One in-process asyncio loop, started in the FastAPI lifespan — the pattern
every scheduled job in this codebase uses (epms-api's NC sync, the daily
follow-up, the agreement overdue scan). No Celery, no APScheduler.

Two sweeps per tick:

* statutory deadlines approaching or past — the Ministry's forty-eight hours,
  WSIB's three business days, and every certificate expiry, which all live in
  the same table and are therefore chased by the same code.
* corrective actions approaching or past their due date, on the HSE Manager's
  ladder.

Escalation is driven by comparing the level a record has *reached* against the
level already recorded on it, so a tick only acts on a change. Running the
sweep twice in a row raises nothing the second time, which matters because the
interval is re-read from configuration on every tick and can be shortened.

The interval lives in ehs_config and is re-read every tick, so changing it in
Settings takes effect within the minute without a restart. Zero disables the
sweep entirely — the same convention as the NC sync scheduler.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.action import Action
from app.models.config import EhsConfig
from app.models.mirrors import User
from app.models.statutory import StatutoryDeadline
from app.services import deadlines as deadline_service
from app.services import notify
from app.services import tasks as task_service
from app.crud import asset as asset_crud
from app.crud import course as course_crud
from app.crud import permit as permit_crud
from app.crud import toolbox as toolbox_crud
from app.services import schedule_sweep
from app.services.escalation import CapaLadder, capa_level, statutory_level

logger = logging.getLogger(__name__)

# How often the loop wakes to look. Whether it does anything is decided by the
# interval in ehs_config.
TICK_SECONDS = settings.SCHEDULER_TICK_SECONDS


@dataclass
class SweepResult:
    statutory_escalated: int = 0
    actions_escalated: int = 0
    inspections_generated: int = 0
    inspections_missed: int = 0
    talks_chased: int = 0
    permits_expired: int = 0
    checks_generated: int = 0
    checks_missed: int = 0
    courses_reminded: int = 0
    skipped_reason: str | None = None

    def __str__(self) -> str:
        if self.skipped_reason:
            return f"skipped ({self.skipped_reason})"
        return (f"{self.statutory_escalated} statutory, "
                f"{self.actions_escalated} actions, "
                f"{self.inspections_generated} inspections due, "
                f"{self.inspections_missed} missed, "
                f"{self.talks_chased} talks chased, "
                f"{self.checks_generated} equipment checks due, "
                f"{self.checks_missed} checks missed, "
                f"{self.courses_reminded} courses reminded, "
                f"{self.permits_expired} permits expired")


async def _load_config(db: AsyncSession) -> EhsConfig | None:
    return (await db.execute(select(EhsConfig).where(EhsConfig.id == 1))).scalar_one_or_none()


# Which document a deadline's task should point at lives with the deadlines
# service: raising the task and closing it have to agree about where it hangs,
# and they did not while each kept its own answer.


async def sweep_statutory(db: AsyncSession, now: datetime) -> int:
    """Raise a task for each statutory deadline that has crossed a threshold."""
    rows = (await db.execute(
        select(StatutoryDeadline).where(StatutoryDeadline.satisfied_at.is_(None))
    )).scalars().all()

    raised = 0
    for deadline in rows:
        level = statutory_level(deadline.due_at, now)
        if level <= deadline.escalation_level:
            continue
        deadline.escalation_level = level
        overdue = level >= 3
        remaining = deadline.due_at - now
        when = ("overdue" if overdue
                else f"due in {int(remaining.total_seconds() // 3600)}h")
        doc_type = deadline_service.DEADLINE_DOC_TYPE.get(
            deadline.source_type, task_service.DOC_INCIDENT)
        # The obligation's own name leads the title, and is how its task is
        # found again — here, and by `satisfy()` when it closes it. Derived in
        # one place so the two cannot drift: one record can owe two of these,
        # and matching on the document alone would fold them into one prompt.
        lead = deadline_service.task_lead(deadline.kind)
        title = f"{lead}{when}"
        description = (f"{deadline.regulation_ref or 'Statutory obligation'}. "
                       f"Due {deadline.due_at.isoformat()}.")
        priority = "high" if level >= 2 else "normal"

        # ★ One obligation, one prompt. Escalating used to add a task rather
        # than change the one already there, so a single deadline that crossed
        # all three thresholds left three open tasks saying the same thing in
        # three tones — and on the test database one incident had six. They
        # were invisible while nothing read role-assigned tasks; the moment
        # that was fixed they became most of what the HSE Manager could see.
        existing = next(
            (t for t in await task_service.open_tasks_for(
                db, doc_type=doc_type, doc_id=deadline.source_id)
             if t.type == task_service.TASK_STATUTORY
             and (t.title or "").startswith(lead)), None)
        if existing is not None:
            existing.title = title
            existing.description = description
            existing.priority = priority
        else:
            await task_service.open_task(
                db,
                task_type=task_service.TASK_STATUTORY,
                doc_type=doc_type,
                doc_id=deadline.source_id,
                doc_number=deadline.source_ref or str(deadline.source_id)[:8],
                title=title,
                assigned_role="ehs_manager",
                description=description,
                priority=priority,
            )
        # HSE gets this by email as well as by inbox. It is the one category
        # where a list somebody has not opened today is not good enough.
        await notify.statutory_escalated(db, deadline, overdue=overdue)
        raised += 1
    return raised


async def sweep_actions(db: AsyncSession, now: datetime, ladder: CapaLadder) -> int:
    """Escalate corrective actions along the HSE Manager's ladder.

    Level 1 goes to the owner, who already has the original task, so it only
    raises the priority rather than adding a second one. Levels 2 and 3 widen
    the audience, which is the point of escalating at all.
    """
    rows = (await db.execute(
        select(Action).where(Action.status.in_(("open", "in_progress", "pending_verification")))
    )).scalars().all()

    today = now.date()
    raised = 0
    for action in rows:
        level = capa_level(action.due_date, today, ladder)
        if level <= action.escalation_level:
            continue
        previous, action.escalation_level = action.escalation_level, level
        action.last_escalated_at = now

        if level == 1:
            for task in await task_service.open_tasks_for(
                db, doc_type=task_service.DOC_ACTION, doc_id=action.id
            ):
                task.priority = "high"
            raised += 1
            continue

        days_late = (today - action.due_date).days
        if level == 2:
            supervisor_id = (await db.execute(
                select(User.supervisor_id).where(User.id == action.owner_id)
            )).scalar()
            await task_service.open_task(
                db,
                task_type=task_service.TASK_DO_ACTION,
                doc_type=task_service.DOC_ACTION,
                doc_id=action.id,
                doc_number=action.action_no,
                title=f"Overdue {days_late}d: {action.title}",
                assigned_role="area_supervisor",
                assigned_user_id=supervisor_id,
                description=f"{action.owner_name or 'The owner'} has not closed this.",
                priority="high",
            )
            await notify.action_escalated(
                db, action, level=level, days_late=days_late,
                supervisor_id=supervisor_id)
        else:
            await task_service.open_task(
                db,
                task_type=task_service.TASK_DO_ACTION,
                doc_type=task_service.DOC_ACTION,
                doc_id=action.id,
                doc_number=action.action_no,
                title=f"Overdue {days_late}d and escalated: {action.title}",
                assigned_role="ehs_manager",
                description=f"Past the {ladder.manager_days}-day threshold.",
                priority="high",
            )
            await notify.action_escalated(
                db, action, level=level, days_late=days_late, supervisor_id=None)
        logger.info("Action %s escalated %d -> %d", action.action_no, previous, level)
        raised += 1
    return raised


async def run_tick(now: datetime | None = None) -> SweepResult:
    """One sweep. Safe to call directly — the tests do."""
    now = now or datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        config = await _load_config(db)
        if config is None:
            return SweepResult(skipped_reason="no ehs_config row")
        if config.statutory_scan_interval_minutes <= 0:
            return SweepResult(skipped_reason="disabled in settings")

        ladder = CapaLadder(
            remind_before_days=config.capa_remind_before_days,
            supervisor_days=config.capa_escalate_supervisor_days,
            manager_days=config.capa_escalate_manager_days,
        )
        # Scheduled inspections ride the same tick: creating the occurrences
        # that are due and marking the ones that were not done. Both passes are
        # idempotent, so sharing a tick with the escalation sweeps is safe even
        # when the interval is shortened.
        today = now.astimezone(timezone.utc).date()
        generated = await schedule_sweep.generate_due(db, today=today)
        missed = await schedule_sweep.chase_overdue(db, today=today)
        # HSE: biweekly reminders until the toolbox talk is signed off.
        nudged = await toolbox_crud.chase_outstanding(db, today=today)
        # Safety equipment runs its own cycle rather than the form scheduler's:
        # "when was extinguisher 47 last checked" is per asset, and a rotation
        # cannot express it.
        checks = await asset_crud.generate_due(db, today=today)
        checks_missed = await asset_crud.chase_overdue(db, today=today)
        # HSE: attendees are reminded the day before a course runs.
        reminded = await course_crud.remind_upcoming(db, today=today)
        # Reconciles the stored status with the clock. Not what makes a
        # permit expire — that is computed on every read — but it raises
        # the task that says one lapsed without being closed out.
        expired = await permit_crud.expire_lapsed(db, now=now)

        result = SweepResult(
            statutory_escalated=await sweep_statutory(db, now),
            actions_escalated=await sweep_actions(db, now, ladder),
            inspections_generated=len(generated),
            inspections_missed=len(missed),
            talks_chased=len(nudged),
            checks_generated=len(checks),
            checks_missed=len(checks_missed),
            courses_reminded=len(reminded),
            permits_expired=len(expired),
        )
        await db.commit()
        return result


async def _loop() -> None:
    # Measured from the start of the previous run, not its end, so a slow sweep
    # does not push the next one further and further out.
    last_started: datetime | None = None
    while True:
        try:
            now = datetime.now(timezone.utc)
            async with AsyncSessionLocal() as db:
                config = await _load_config(db)
                interval = config.statutory_scan_interval_minutes if config else 0
            due = (
                interval > 0
                and (last_started is None
                     or (now - last_started).total_seconds() >= interval * 60)
            )
            if due:
                last_started = now
                result = await run_tick(now)
                logger.info("Safety sweep: %s", result)
        except asyncio.CancelledError:
            # Must propagate, or shutdown hangs and the session never unwinds.
            raise
        except Exception:
            logger.exception("Safety sweep failed; will retry next tick")
        await asyncio.sleep(TICK_SECONDS)


def start() -> asyncio.Task | None:
    if not settings.SCHEDULER_ENABLED:
        logger.info("Safety scheduler disabled by configuration")
        return None
    return asyncio.create_task(_loop(), name="ehs-scheduler")


async def stop(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
