"""Scheduled background jobs (PRD VMS-PR-012/-019, VMS-CO-010/-011).

These test the pure job functions directly with a fixed `now` and a real
test-DB session — no timing, no live SMTP. The notify_* email helpers are
monkeypatched to record calls + return True (delivery attempted) so we can
assert the idempotency-flag behavior without an SMTP server.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as session_module
from app.core.config import settings
from app.models.visit import AccessArea, Visit, VisitPurpose, VisitStatus
from app.models.visitor import Visitor, VisitorType
from app.services import notifications as notifications_svc
from app.services import scheduled_jobs as jobs

from tests.conftest import make_user

UTC = timezone.utc


# ── Builders ────────────────────────────────────────────────────────────────-

def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _mk_visitor(test_engine) -> Visitor:
    async with _factory(test_engine)() as db:
        v = Visitor(
            first_name="Jo", last_name=f"V{uuid.uuid4().hex[:4]}",
            company_name="ACME Foods", visitor_type=VisitorType.supplier,
        )
        db.add(v)
        await db.commit()
        await db.refresh(v)
        return v


async def _mk_visit(test_engine, *, host, visitor, status, visit_date,
                    planned_arrival, planned_departure=None, actual_arrival=None,
                    access_area=AccessArea.office, **kw) -> Visit:
    async with _factory(test_engine)() as db:
        v = Visit(
            visitor_id=visitor.id, host_id=host.id, created_by=host.id,
            visit_date=visit_date, planned_arrival=planned_arrival,
            planned_departure=planned_departure, actual_arrival=actual_arrival,
            visit_purpose=VisitPurpose.meeting, access_area=access_area,
            status=status, **kw,
        )
        db.add(v)
        await db.commit()
        await db.refresh(v)
        return v


async def _get(test_engine, visit_id) -> Visit:
    async with _factory(test_engine)() as db:
        return (await db.execute(select(Visit).where(Visit.id == visit_id))).scalar_one()


@pytest.fixture
def captured_emails(monkeypatch):
    """Patch every scheduler notify_* helper to record calls + report delivered."""
    calls: dict[str, list] = {"reminder": [], "overdue": [], "escalation": []}

    async def _reminder(db, *, visit, visitor, host):
        calls["reminder"].append(visit.id)
        return True

    async def _overdue(db, *, visit, visitor, host):
        calls["overdue"].append(visit.id)
        return True

    async def _escalation(db, *, visit, visitor, host, manager):
        calls["escalation"].append((visit.id, manager.id))
        return True

    monkeypatch.setattr(notifications_svc, "notify_host_visit_reminder", _reminder)
    monkeypatch.setattr(notifications_svc, "notify_host_overdue", _overdue)
    monkeypatch.setattr(notifications_svc, "notify_manager_overdue_escalation", _escalation)
    return calls


# ── VMS-PR-019: auto no-show ────────────────────────────────────────────────-

async def test_marks_confirmed_visit_no_show_after_2h(test_engine):
    host = await make_user(test_engine, role="requester")
    visitor = await _mk_visitor(test_engine)
    now = datetime(2026, 6, 15, 16, 0, tzinfo=UTC)
    visit = await _mk_visit(
        test_engine, host=host, visitor=visitor, status=VisitStatus.confirmed,
        visit_date=date(2026, 6, 15),
        planned_arrival=now - timedelta(hours=3),  # 3h ago, never arrived
    )
    async with _factory(test_engine)() as db:
        marked = await jobs.mark_no_shows(db, now=now)
        await db.commit()
    assert visit.id in marked
    assert (await _get(test_engine, visit.id)).status == VisitStatus.no_show


async def test_no_show_skips_checked_in_and_recent(test_engine):
    host = await make_user(test_engine, role="requester")
    visitor = await _mk_visitor(test_engine)
    now = datetime(2026, 6, 15, 16, 0, tzinfo=UTC)
    # Already checked in → not a no-show.
    arrived = await _mk_visit(
        test_engine, host=host, visitor=visitor, status=VisitStatus.checked_in,
        visit_date=date(2026, 6, 15), planned_arrival=now - timedelta(hours=3),
        actual_arrival=now - timedelta(hours=2),
    )
    # Only 1h late → inside the 2h grace window.
    recent = await _mk_visit(
        test_engine, host=host, visitor=visitor, status=VisitStatus.confirmed,
        visit_date=date(2026, 6, 15), planned_arrival=now - timedelta(hours=1),
    )
    async with _factory(test_engine)() as db:
        marked = await jobs.mark_no_shows(db, now=now)
        await db.commit()
    assert arrived.id not in marked
    assert recent.id not in marked
    assert (await _get(test_engine, recent.id)).status == VisitStatus.confirmed


# ── VMS-PR-012: day-before reminder ─────────────────────────────────────────-

def _tomorrow_local(now: datetime) -> date:
    return (now.astimezone(ZoneInfo(settings.REPORT_TIMEZONE)) + timedelta(days=1)).date()


async def test_day_before_reminder_sets_flag_once(test_engine, captured_emails):
    host = await make_user(test_engine, role="requester")
    visitor = await _mk_visitor(test_engine)
    now = datetime(2026, 6, 15, 18, 0, tzinfo=UTC)
    visit = await _mk_visit(
        test_engine, host=host, visitor=visitor, status=VisitStatus.confirmed,
        visit_date=_tomorrow_local(now),
        planned_arrival=datetime(2026, 6, 16, 13, 0, tzinfo=UTC),
    )
    # Note: the suite shares one DB, so other confirmed visits dated the same
    # day may also match — assert on THIS visit only, not exact list equality.
    async with _factory(test_engine)() as db:
        first = await jobs.send_day_before_reminders(db, now=now)
        await db.commit()
    assert visit.id in first
    assert captured_emails["reminder"].count(visit.id) == 1
    assert (await _get(test_engine, visit.id)).reminder_sent_at is not None

    # Second run is a no-op for THIS visit — flag already set.
    async with _factory(test_engine)() as db:
        second = await jobs.send_day_before_reminders(db, now=now)
        await db.commit()
    assert visit.id not in second
    assert captured_emails["reminder"].count(visit.id) == 1  # still just one send


async def test_day_before_reminder_skips_non_tomorrow(test_engine, captured_emails):
    host = await make_user(test_engine, role="requester")
    visitor = await _mk_visitor(test_engine)
    now = datetime(2026, 6, 15, 18, 0, tzinfo=UTC)
    # Visit is today, not tomorrow → no reminder.
    visit = await _mk_visit(
        test_engine, host=host, visitor=visitor, status=VisitStatus.confirmed,
        visit_date=now.astimezone(ZoneInfo(settings.REPORT_TIMEZONE)).date(),
        planned_arrival=now + timedelta(hours=2),
    )
    async with _factory(test_engine)() as db:
        sent = await jobs.send_day_before_reminders(db, now=now)
        await db.commit()
    assert visit.id not in sent
    assert visit.id not in captured_emails["reminder"]


# ── VMS-CO-010: 1h overdue reminder ─────────────────────────────────────────-

async def test_overdue_reminder_fires_after_1h(test_engine, captured_emails):
    host = await make_user(test_engine, role="requester")
    visitor = await _mk_visitor(test_engine)
    now = datetime(2026, 6, 15, 20, 0, tzinfo=UTC)
    visit = await _mk_visit(
        test_engine, host=host, visitor=visitor, status=VisitStatus.checked_in,
        visit_date=date(2026, 6, 15),
        planned_arrival=now - timedelta(hours=5),
        planned_departure=now - timedelta(hours=2),  # 2h overdue
        actual_arrival=now - timedelta(hours=5),
    )
    async with _factory(test_engine)() as db:
        sent = await jobs.send_overdue_reminders(db, now=now)
        await db.commit()
    assert visit.id in sent
    assert captured_emails["overdue"] == [visit.id]
    assert (await _get(test_engine, visit.id)).overdue_reminder_sent_at is not None

    # Idempotent second run.
    async with _factory(test_engine)() as db:
        again = await jobs.send_overdue_reminders(db, now=now)
        await db.commit()
    assert again == []
    assert captured_emails["overdue"] == [visit.id]


async def test_overdue_reminder_skips_not_yet_overdue(test_engine, captured_emails):
    host = await make_user(test_engine, role="requester")
    visitor = await _mk_visitor(test_engine)
    now = datetime(2026, 6, 15, 20, 0, tzinfo=UTC)
    # Departed 30min ago — under the 1h threshold.
    visit = await _mk_visit(
        test_engine, host=host, visitor=visitor, status=VisitStatus.checked_in,
        visit_date=date(2026, 6, 15),
        planned_arrival=now - timedelta(hours=2),
        planned_departure=now - timedelta(minutes=30),
        actual_arrival=now - timedelta(hours=2),
    )
    async with _factory(test_engine)() as db:
        sent = await jobs.send_overdue_reminders(db, now=now)
        await db.commit()
    assert visit.id not in sent


# ── VMS-CO-011: 4h overdue escalation to dept manager ───────────────────────-

async def test_overdue_escalation_emails_dept_manager(test_engine, captured_emails):
    dept_id = uuid.uuid4()
    manager = await make_user(test_engine, role="dept_manager", department_id=dept_id)
    host = await make_user(test_engine, role="requester", department_id=dept_id)
    visitor = await _mk_visitor(test_engine)
    now = datetime(2026, 6, 15, 22, 0, tzinfo=UTC)
    visit = await _mk_visit(
        test_engine, host=host, visitor=visitor, status=VisitStatus.checked_in,
        visit_date=date(2026, 6, 15),
        planned_arrival=now - timedelta(hours=8),
        planned_departure=now - timedelta(hours=5),  # 5h overdue
        actual_arrival=now - timedelta(hours=8),
    )
    async with _factory(test_engine)() as db:
        sent = await jobs.escalate_overdue(db, now=now)
        await db.commit()
    assert visit.id in sent
    assert captured_emails["escalation"] == [(visit.id, manager.id)]
    assert (await _get(test_engine, visit.id)).overdue_escalated_at is not None


async def test_overdue_escalation_no_manager_leaves_flag_unset(test_engine, captured_emails):
    # Host with a department but no dept_manager in it → nobody to escalate to.
    host = await make_user(test_engine, role="requester", department_id=uuid.uuid4())
    visitor = await _mk_visitor(test_engine)
    now = datetime(2026, 6, 15, 22, 0, tzinfo=UTC)
    visit = await _mk_visit(
        test_engine, host=host, visitor=visitor, status=VisitStatus.checked_in,
        visit_date=date(2026, 6, 15),
        planned_arrival=now - timedelta(hours=8),
        planned_departure=now - timedelta(hours=5),
        actual_arrival=now - timedelta(hours=8),
    )
    async with _factory(test_engine)() as db:
        sent = await jobs.escalate_overdue(db, now=now)
        await db.commit()
    assert visit.id not in sent
    assert captured_emails["escalation"] == []
    assert (await _get(test_engine, visit.id)).overdue_escalated_at is None


# ── Orchestrator ────────────────────────────────────────────────────────────-

async def test_run_all_returns_summary(test_engine, captured_emails):
    host = await make_user(test_engine, role="requester")
    visitor = await _mk_visitor(test_engine)
    now = datetime(2026, 6, 15, 16, 0, tzinfo=UTC)
    await _mk_visit(
        test_engine, host=host, visitor=visitor, status=VisitStatus.confirmed,
        visit_date=date(2026, 6, 15), planned_arrival=now - timedelta(hours=3),
    )
    async with _factory(test_engine)() as db:
        summary = await jobs.run_all(db, now=now)
        await db.commit()
    assert summary["no_shows"] >= 1
    assert set(summary) == {"no_shows", "reminders", "overdue_reminders", "escalations"}
