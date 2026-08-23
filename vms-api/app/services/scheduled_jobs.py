"""Time-based background jobs (PRD VMS-PR-012/-019, VMS-CO-009/-010/-011).

Pure async functions: each takes an `AsyncSession` + an explicit `now` and
mutates rows / dispatches email. No timing, no loop — the scheduler loop
(`app.services.scheduler`) calls `run_all` on a tick and owns the commit, so
these are trivially unit-testable with a fixed clock.

Idempotency:
  - no-show is a status transition (confirmed → no_show), naturally one-shot.
  - reminders / escalation persist a timestamp flag on the visit
    (`reminder_sent_at`, `overdue_reminder_sent_at`, `overdue_escalated_at`),
    set only when email delivery is attempted, so a transient SMTP outage
    retries on the next tick instead of silently dropping the notice.
  - the day-before reminder and the escalation are one-shot; the overdue
    Host reminder re-sends every `OVERDUE_REMINDER_REPEAT` (24h) until the
    visitor is checked out — `overdue_reminder_sent_at` is its LAST send
    time, not a one-shot flag.

"Overdue" (VMS-CO-009) is a derived state — a checked_in visit past its
planned_departure — not a stored status (there is no `overdue` VisitStatus).
The dashboard already surfaces the count (`crud.visit.count_overdue`); these
jobs add the reminder (VMS-CO-010) and escalation (VMS-CO-011) on top of it.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud import audit as audit_crud
from app.models.user_mirror import User
from app.models.visit import Visit, VisitStatus
from app.models.visitor import Visitor
from app.services import notifications as notifications_svc
from app.services import visit_tasks

log = logging.getLogger(__name__)

# Synthetic actor for system-initiated audit rows (vms_audit_logs.user_id has
# no FK, so a sentinel UUID is safe). Lets auditors tell scheduler actions
# apart from human ones.
SYSTEM_ACTOR_ID = uuid.UUID(int=0)
SYSTEM_ACTOR_NAME = "VMS Scheduler"

# Thresholds (PRD). Tunable here — not schema.
NO_SHOW_GRACE = timedelta(hours=2)         # VMS-PR-019
OVERDUE_REMINDER_AFTER = timedelta(hours=1)  # VMS-CO-010
OVERDUE_ESCALATE_AFTER = timedelta(hours=4)  # VMS-CO-011
OVERDUE_REMINDER_REPEAT = timedelta(hours=24)  # re-nag the Host daily until checkout


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


async def _visitor_and_host(
    db: AsyncSession, visit: Visit,
) -> tuple[Visitor | None, User | None]:
    visitor = (await db.execute(
        select(Visitor).where(Visitor.id == visit.visitor_id)
    )).scalar_one_or_none()
    host = (await db.execute(
        select(User).where(User.id == visit.host_id)
    )).scalar_one_or_none()
    return visitor, host


async def _dept_manager(db: AsyncSession, host: User | None) -> User | None:
    """Active dept_manager in the Host's department, or None."""
    if host is None or host.department_id is None:
        return None
    return (await db.execute(
        select(User).where(
            User.department_id == host.department_id,
            User.role == "dept_manager",
            User.is_active.is_(True),
        ).limit(1)
    )).scalar_one_or_none()


# ── VMS-PR-019: auto no-show ────────────────────────────────────────────────-

async def mark_no_shows(db: AsyncSession, *, now: datetime | None = None) -> list[uuid.UUID]:
    """Flip confirmed visits that never checked in (planned_arrival + 2h < now)
    to `no_show`. Each transition is audit-logged under the system actor."""
    now = _now(now)
    cutoff = now - NO_SHOW_GRACE
    rows = (await db.execute(
        select(Visit).where(
            Visit.status == VisitStatus.confirmed,
            Visit.actual_arrival.is_(None),
            Visit.planned_arrival < cutoff,
        )
    )).scalars().all()

    marked: list[uuid.UUID] = []
    for visit in rows:
        before = audit_crud.snapshot(visit)
        visit.status = VisitStatus.no_show
        await db.flush()
        await audit_crud.log_event(
            db,
            user_id=SYSTEM_ACTOR_ID,
            user_name=SYSTEM_ACTOR_NAME,
            action_type="visit.no_show",
            entity_type="visit",
            entity_id=visit.id,
            ip_address="system",
            old_value=before,
            new_value=audit_crud.snapshot(visit),
            notes="Auto no-show: not checked in within 2h of planned arrival",
        )
        marked.append(visit.id)
    if marked:
        log.info("Scheduler: marked %d visit(s) no-show", len(marked))
    return marked


# ── VMS-PR-012: day-before reminder ─────────────────────────────────────────-

def _reminder_target_date(now: datetime):
    """Calendar date of 'tomorrow' in the plant's local timezone."""
    try:
        local = ZoneInfo(settings.REPORT_TIMEZONE)
    except Exception:  # noqa: BLE001 — bad tz config degrades to UTC
        local = timezone.utc
    return (now.astimezone(local) + timedelta(days=1)).date()


async def send_day_before_reminders(
    db: AsyncSession, *, now: datetime | None = None,
) -> list[uuid.UUID]:
    """Email the Host the day before each confirmed visit. One-shot via
    `reminder_sent_at`."""
    now = _now(now)
    target = _reminder_target_date(now)
    rows = (await db.execute(
        select(Visit).where(
            Visit.status == VisitStatus.confirmed,
            Visit.visit_date == target,
            Visit.reminder_sent_at.is_(None),
        )
    )).scalars().all()

    sent: list[uuid.UUID] = []
    for visit in rows:
        visitor, host = await _visitor_and_host(db, visit)
        if visitor is None:
            continue
        if await notifications_svc.notify_host_visit_reminder(
            db, visit=visit, visitor=visitor, host=host,
        ):
            visit.reminder_sent_at = now
            await db.flush()
            sent.append(visit.id)
    if sent:
        log.info("Scheduler: sent %d day-before reminder(s)", len(sent))
    return sent


# ── VMS-CO-010: 1h overdue reminder ─────────────────────────────────────────-

async def send_overdue_reminders(
    db: AsyncSession, *, now: datetime | None = None,
) -> list[uuid.UUID]:
    """Remind the Host when a checked-in visitor is 1h+ past planned
    departure, then re-send every `OVERDUE_REMINDER_REPEAT` until checkout.
    `overdue_reminder_sent_at` holds the LAST send time (not a one-shot
    flag), so the nag naturally stops once the visit leaves `checked_in`."""
    now = _now(now)
    cutoff = now - OVERDUE_REMINDER_AFTER
    resend_before = now - OVERDUE_REMINDER_REPEAT
    rows = (await db.execute(
        select(Visit).where(
            Visit.status == VisitStatus.checked_in,
            Visit.planned_departure.is_not(None),
            Visit.planned_departure < cutoff,
            or_(
                Visit.overdue_reminder_sent_at.is_(None),
                Visit.overdue_reminder_sent_at < resend_before,
            ),
        )
    )).scalars().all()

    sent: list[uuid.UUID] = []
    for visit in rows:
        visitor, host = await _visitor_and_host(db, visit)
        if visitor is None:
            continue
        if await notifications_svc.notify_host_overdue(
            db, visit=visit, visitor=visitor, host=host,
        ):
            visit.overdue_reminder_sent_at = now
            await db.flush()
            sent.append(visit.id)
    if sent:
        log.info("Scheduler: sent %d overdue reminder(s)", len(sent))
    return sent


# ── VMS-CO-011: 4h overdue escalation to dept manager ───────────────────────-

async def escalate_overdue(
    db: AsyncSession, *, now: datetime | None = None,
) -> list[uuid.UUID]:
    """Escalate to the Host's Department Manager when a visitor is 4h+ past
    planned departure. One-shot via `overdue_escalated_at`. When no active
    dept_manager exists for the Host's department the flag stays unset so the
    escalation fires once a manager is assigned."""
    now = _now(now)
    cutoff = now - OVERDUE_ESCALATE_AFTER
    rows = (await db.execute(
        select(Visit).where(
            Visit.status == VisitStatus.checked_in,
            Visit.planned_departure.is_not(None),
            Visit.planned_departure < cutoff,
            Visit.overdue_escalated_at.is_(None),
        )
    )).scalars().all()

    escalated: list[uuid.UUID] = []
    for visit in rows:
        visitor, host = await _visitor_and_host(db, visit)
        if visitor is None:
            continue
        manager = await _dept_manager(db, host)
        if manager is None:
            continue
        if await notifications_svc.notify_manager_overdue_escalation(
            db, visit=visit, visitor=visitor, host=host, manager=manager,
        ):
            visit.overdue_escalated_at = now
            await db.flush()
            escalated.append(visit.id)
    if escalated:
        log.info("Scheduler: escalated %d overdue visit(s)", len(escalated))
    return escalated


# ── Orchestrator ────────────────────────────────────────────────────────────-

async def run_all(db: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """Run every job in order and return a count summary. Does NOT commit —
    the caller (scheduler tick / manual trigger) owns the transaction."""
    now = _now(now)
    no_shows = await mark_no_shows(db, now=now)
    reminders = await send_day_before_reminders(db, now=now)
    overdue_reminders = await send_overdue_reminders(db, now=now)
    escalations = await escalate_overdue(db, now=now)
    # 访客签出 / PPE 备货任务的收口。放在最后:mark_no_shows 可能刚把一批 visit
    # 推出「等待到访」状态,它们的备货任务应当在同一趟里一起关掉。
    settled_tasks = await visit_tasks.close_settled_visit_tasks(db)
    return {
        "no_shows": len(no_shows),
        "reminders": len(reminders),
        "overdue_reminders": len(overdue_reminders),
        "escalations": len(escalations),
        "settled_tasks": settled_tasks,
    }
