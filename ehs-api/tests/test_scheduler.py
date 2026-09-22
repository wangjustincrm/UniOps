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
    owner = await make_user(test_engine, role="requester")
    await _config(db_session, statutory_scan_interval_minutes=60)
    action = await _action(db_session, due_date=NOW.date() + timedelta(days=2), owner_id=owner.id)
    await task_service.open_task(
        db_session, task_type=task_service.TASK_DO_ACTION,
        doc_type=task_service.DOC_ACTION, doc_id=action.id,
        doc_number=action.action_no, title=action.title,
        assigned_role="requester", assigned_user_id=owner.id)
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
    owner = await make_user(test_engine, role="requester")
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
    owner = await make_user(test_engine, role="requester")
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
    owner = await make_user(test_engine, role="requester")
    await _config(db_session, statutory_scan_interval_minutes=60,
                  capa_remind_before_days=0, capa_escalate_supervisor_days=2,
                  capa_escalate_manager_days=4)
    action = await _action(db_session, due_date=NOW.date() - timedelta(days=2),
                           owner_id=owner.id, level=1)
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", lambda: _Ctx(db_session))

    await scheduler.run_tick(NOW)
    assert action.escalation_level == 2


async def test_a_check_can_be_recorded_when_none_was_due(hse_manager, test_engine):
    """The schedule says when a check is owed, not when one may happen.

    Recording was only possible against an occurrence the sweep had already
    raised. With nothing due on anything — every asset weeks out — pressing
    "record a check" had no row to write to and saved nothing, which the second
    acceptance round reported as checks not being logged anywhere. Somebody
    walks past an extinguisher and looks at it; that is a check.
    """
    from sqlalchemy import text
    _, client = hse_manager

    asset = (await client.post("/api/v1/safety-assets", json={
        "name": "Extinguisher by the dryer door",
        "inspection_frequency": "monthly",
    })).json()

    async with test_engine.begin() as conn:
        due_before = (await conn.execute(text(
            "SELECT count(*) FROM ehs_asset_inspections WHERE asset_id = :a"),
            {"a": asset["id"]})).scalar()
    assert due_before == 0, "nothing is due on a brand new asset"

    done = await client.post(f"/api/v1/safety-assets/{asset['id']}/inspections",
                             json={"result": "passed", "notes": "Gauge in the green"})
    assert done.status_code == 201, done.text
    assert done.json()["result"] == "passed"

    after = (await client.get(f"/api/v1/safety-assets/{asset['id']}")).json()
    assert after["last_inspected_on"] is not None, "the check has to count for something"
    assert after["status"] == "in_service"


async def test_failing_an_ad_hoc_check_takes_the_item_out_of_service(hse_manager):
    """Same consequences as a scheduled one, or it is not really a check."""
    _, client = hse_manager
    asset = (await client.post("/api/v1/safety-assets", json={
        "name": "Eye wash by the caustic line",
        "inspection_frequency": "monthly",
    })).json()

    await client.post(f"/api/v1/safety-assets/{asset['id']}/inspections",
                      json={"result": "failed", "notes": "No flow"})

    after = (await client.get(f"/api/v1/safety-assets/{asset['id']}")).json()
    assert after["status"] == "out_of_service"


async def test_a_drill_records_its_cause_its_signature_and_its_actions(
        hse_manager, db_session):
    """What turns an evaluation into evidence.

    Prose said what worked and what did not and stopped there. HSE asked for
    the three things an inspector actually asks for: why it went that way,
    drawn from the same root cause list the investigations use so a recurring
    cause is countable across both; who says this is what happened; and
    something to do about it.
    """
    import uuid as _uuid
    from datetime import date

    from app.models.vocabulary import Vocabulary, VocabularyItem

    db_session.add(Vocabulary(code="root_cause", name="Root Cause", is_hierarchical=True))
    await db_session.flush()
    cause = _uuid.uuid4()
    db_session.add(VocabularyItem(
        id=cause, vocabulary_code="root_cause", code="no_drill_practice",
        label="Procedure not practised", sort_order=10, path="Procedure not practised"))
    await db_session.commit()

    user, client = hse_manager
    created = await client.post("/api/v1/drills", json={
        "drill_type": "evacuation",
        "scenario": "Fire in the dryer bay.",
        "held_on": date.today().isoformat(),
    })
    assert created.status_code in (200, 201), created.text
    drill_id = created.json()["id"]

    saved = await client.patch(f"/api/v1/drills/{drill_id}", json={
        # Four minutes fifteen, entered as minutes and seconds and stored as
        # seconds — the trend and the figures are drawn from the column.
        "evacuation_seconds": 4 * 60 + 15,
        "all_accounted_for": True,
        "what_failed": "The north fire exit was blocked by pallets.",
        "root_cause_id": str(cause),
        "root_cause_label": "Procedure not practised",
        "signature": "data:image/png;base64,iVBORw0KGgo=",
    })
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["evacuation_seconds"] == 255
    assert body["root_cause_label"] == "Procedure not practised"
    assert body["root_cause_id"] == str(cause)

    # The name and the time come from the server. A signature carrying a name
    # the client chose is not evidence of anything.
    assert body["signed_at"] is not None
    assert body["signed_by_name"] == user.full_name

    raised = await client.post("/api/v1/actions", json={
        "source_type": "drill", "source_id": drill_id,
        "title": "Clear the pallets from the north fire exit",
        "owner_id": str(user.id),
        "due_date": date.today().isoformat(),
    })
    assert raised.status_code in (200, 201), raised.text

    listed = (await client.get(
        f"/api/v1/actions?source_type=drill&source_id={drill_id}")).json()
    rows = listed if isinstance(listed, list) else listed.get("items", [])
    assert any(r["title"].startswith("Clear the pallets") for r in rows), (
        "an action raised from a drill has to come back against that drill — "
        f"got {rows}")


async def test_clearing_a_drill_signature_clears_who_signed_it(hse_manager):
    """Otherwise it shows as signed by somebody with nothing under it."""
    from datetime import date

    _, client = hse_manager
    drill_id = (await client.post("/api/v1/drills", json={
        "drill_type": "evacuation", "scenario": "Spill in receiving.",
        "held_on": date.today().isoformat(),
    })).json()["id"]

    await client.patch(f"/api/v1/drills/{drill_id}",
                       json={"signature": "data:image/png;base64,iVBORw0KGgo="})
    cleared = await client.patch(f"/api/v1/drills/{drill_id}", json={"signature": None})
    assert cleared.json()["signed_by_name"] is None
    assert cleared.json()["signed_at"] is None
