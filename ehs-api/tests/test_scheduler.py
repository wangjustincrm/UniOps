"""The sweep that chases deadlines.

run_tick is called directly with a fixed `now` — the loop around it is a
sleep and a re-read of the interval, and testing that would only test asyncio.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import sqlalchemy

from app.models.action import Action
from app.models.config import EhsConfig
from app.models.statutory import StatutoryDeadline
from app.services import tasks as task_service
from app.tasks import scheduler

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


async def _config(db, **kw):
    row = (await db.execute(sqlalchemy.select(EhsConfig))).scalar_one_or_none()
    if row is None:
        row = EhsConfig(id=1)
        db.add(row)
    for k, v in kw.items():
        setattr(row, k, v)
    await db.flush()
    return row


async def _deadline(db, *, due_at, level=0, kind="mol_48h"):
    d = StatutoryDeadline(
        id=uuid.uuid4(), source_type="incident", source_id=uuid.uuid4(),
        source_ref="INC-2026-0001", kind=kind, regulation_ref="OHSA s.51(1)",
        clock_type="calendar", starts_at=due_at - timedelta(hours=48),
        due_at=due_at, escalation_level=level,
    )
    db.add(d)
    await db.flush()
    return d


async def _action(db, *, due_date, owner_id=None, level=0, status="open"):
    a = Action(
        id=uuid.uuid4(), action_no=f"CAPA-TEST-{uuid.uuid4().hex[:6]}",
        source_type="manual", title="Fit a guard", status=status,
        owner_id=owner_id, due_date=due_date, escalation_level=level,
    )
    db.add(a)
    await db.flush()
    return a


async def _tasks(db, doc_id):
    rows = (await db.execute(sqlalchemy.text(
        "SELECT type, assigned_role, priority FROM tasks WHERE document_id = :d"
    ), {"d": str(doc_id)})).mappings().all()
    return [dict(r) for r in rows]


# ── Configuration gates the whole sweep ─────────────────────────────────────

async def test_a_zero_interval_disables_the_sweep(db_session, monkeypatch):
    await _config(db_session, statutory_scan_interval_minutes=0)
    await _deadline(db_session, due_at=NOW - timedelta(hours=1))
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", lambda: _Ctx(db_session))

    result = await scheduler.run_tick(NOW)
    assert result.skipped_reason == "disabled in settings"
    assert result.statutory_escalated == 0


class _Ctx:
    """Hand run_tick the test's savepoint-isolated session."""

    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


# ── Statutory sweep ─────────────────────────────────────────────────────────

async def test_a_distant_deadline_is_left_alone(db_session, monkeypatch):
    await _config(db_session, statutory_scan_interval_minutes=60)
    d = await _deadline(db_session, due_at=NOW + timedelta(days=3))
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", lambda: _Ctx(db_session))

    result = await scheduler.run_tick(NOW)
    assert result.statutory_escalated == 0
    assert d.escalation_level == 0
    assert await _tasks(db_session, d.source_id) == []


async def test_a_deadline_inside_a_day_raises_a_task_for_the_hse_manager(db_session, monkeypatch):
    await _config(db_session, statutory_scan_interval_minutes=60)
    d = await _deadline(db_session, due_at=NOW + timedelta(hours=20))
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", lambda: _Ctx(db_session))

    assert (await scheduler.run_tick(NOW)).statutory_escalated == 1
    assert d.escalation_level == 1
    tasks = await _tasks(db_session, d.source_id)
    assert len(tasks) == 1
    assert tasks[0]["type"] == task_service.TASK_STATUTORY
    assert tasks[0]["assigned_role"] == "ehs_manager"


async def test_an_overdue_deadline_is_high_priority(db_session, monkeypatch):
    await _config(db_session, statutory_scan_interval_minutes=60)
    d = await _deadline(db_session, due_at=NOW - timedelta(hours=2))
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", lambda: _Ctx(db_session))

    await scheduler.run_tick(NOW)
    assert d.escalation_level == 3
    assert (await _tasks(db_session, d.source_id))[0]["priority"] == "high"


async def test_sweeping_twice_raises_nothing_the_second_time(db_session, monkeypatch):
    """The interval is configurable and can be shortened, so a repeat sweep
    must be a no-op rather than a second pile of tasks."""
    await _config(db_session, statutory_scan_interval_minutes=60)
    d = await _deadline(db_session, due_at=NOW + timedelta(hours=20))
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", lambda: _Ctx(db_session))

    assert (await scheduler.run_tick(NOW)).statutory_escalated == 1
    assert (await scheduler.run_tick(NOW)).statutory_escalated == 0
    assert len(await _tasks(db_session, d.source_id)) == 1


async def test_a_satisfied_deadline_is_never_chased(db_session, monkeypatch):
    await _config(db_session, statutory_scan_interval_minutes=60)
    d = await _deadline(db_session, due_at=NOW - timedelta(days=2))
    d.satisfied_at = NOW - timedelta(days=1)
    await db_session.flush()
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", lambda: _Ctx(db_session))

    assert (await scheduler.run_tick(NOW)).statutory_escalated == 0


# ── Corrective action ladder ────────────────────────────────────────────────

async def test_an_action_due_soon_only_raises_the_priority(db_session, monkeypatch, test_engine):
    """Level one goes to the owner, who already has a task — saying it twice
    adds nothing, so the existing task just becomes urgent."""
    from tests.conftest import make_user
    owner = await make_user(test_engine, role="worker")
    await _config(db_session, statutory_scan_interval_minutes=60)
    action = await _action(db_session, due_date=NOW.date() + timedelta(days=2), owner_id=owner.id)
    await task_service.open_task(
        db_session, task_type=task_service.TASK_DO_ACTION,
        doc_type=task_service.DOC_ACTION, doc_id=action.id,
        doc_number=action.action_no, title=action.title,
        assigned_role="worker", assigned_user_id=owner.id)
    await db_session.flush()
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", lambda: _Ctx(db_session))

    assert (await scheduler.run_tick(NOW)).actions_escalated == 1
    assert action.escalation_level == 1
    tasks = await _tasks(db_session, action.id)
    assert len(tasks) == 1, "level one must not add a second task"
    assert tasks[0]["priority"] == "high"


async def test_five_days_overdue_brings_in_the_supervisor(db_session, monkeypatch, test_engine):
    from tests.conftest import make_user
    supervisor = await make_user(test_engine, role="area_supervisor")
    owner = await make_user(test_engine, role="worker")
    await db_session.execute(sqlalchemy.text(
        "UPDATE users SET supervisor_id = :s WHERE id = :o"),
        {"s": str(supervisor.id), "o": str(owner.id)})
    await _config(db_session, statutory_scan_interval_minutes=60)
    action = await _action(db_session, due_date=NOW.date() - timedelta(days=5),
                           owner_id=owner.id, level=1)
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", lambda: _Ctx(db_session))

    assert (await scheduler.run_tick(NOW)).actions_escalated == 1
    assert action.escalation_level == 2
    tasks = await _tasks(db_session, action.id)
    assert tasks[0]["assigned_role"] == "area_supervisor"


async def test_ten_days_overdue_reaches_the_hse_manager(db_session, monkeypatch, test_engine):
    from tests.conftest import make_user
    owner = await make_user(test_engine, role="worker")
    await _config(db_session, statutory_scan_interval_minutes=60)
    action = await _action(db_session, due_date=NOW.date() - timedelta(days=10),
                           owner_id=owner.id, level=2)
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", lambda: _Ctx(db_session))

    await scheduler.run_tick(NOW)
    assert action.escalation_level == 3
    assert (await _tasks(db_session, action.id))[0]["assigned_role"] == "ehs_manager"


async def test_a_closed_action_is_never_escalated(db_session, monkeypatch):
    await _config(db_session, statutory_scan_interval_minutes=60)
    action = await _action(db_session, due_date=NOW.date() - timedelta(days=30),
                           status="closed")
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", lambda: _Ctx(db_session))

    assert (await scheduler.run_tick(NOW)).actions_escalated == 0
    assert action.escalation_level == 0


async def test_thresholds_follow_configuration(db_session, monkeypatch, test_engine):
    """Two days overdue is nothing on the default ladder and a supervisor
    escalation on a stricter one."""
    from tests.conftest import make_user
    owner = await make_user(test_engine, role="worker")
    await _config(db_session, statutory_scan_interval_minutes=60,
                  capa_remind_before_days=0, capa_escalate_supervisor_days=2,
                  capa_escalate_manager_days=4)
    action = await _action(db_session, due_date=NOW.date() - timedelta(days=2),
                           owner_id=owner.id, level=1)
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", lambda: _Ctx(db_session))

    await scheduler.run_tick(NOW)
    assert action.escalation_level == 2
