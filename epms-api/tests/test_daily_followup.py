"""Daily follow-up admin toggle (notification_settings.daily_followup_enabled).

The 08:00 scheduler loop always ticks, but each run must consult the company
config first: OFF (or key absent — every pre-existing prod row) skips the whole
notification pass; ON dispatches a follow-up for every open task.

Same isolation caveats as test_notification_dispatch.py: the shared
``company_config`` row may exist in duplicate, so settings are written to ALL
rows, snapshotted and restored around each test, and assertions filter the
dispatch capture down to this test's own task instead of the global count.
"""
import asyncio
import copy
import uuid

import pytest
from sqlalchemy import update as sa_update

import app.db.session as session_module
from app.crud.config import _DEFAULT_NOTIFICATION_SETTINGS, get_or_create as get_config
from app.models.config import CompanyConfig
from app.models.task import Task
from app.tasks import daily_followup


@pytest.fixture(autouse=True)
async def _restore_notification_settings():
    async with session_module.AsyncSessionLocal() as db:
        cfg = await get_config(db)
        snapshot = copy.deepcopy(cfg.notification_settings)
        await db.commit()
    yield
    async with session_module.AsyncSessionLocal() as db:
        await db.execute(
            sa_update(CompanyConfig).values(notification_settings=copy.deepcopy(snapshot))
        )
        await db.commit()


@pytest.fixture
def captured_dispatches(monkeypatch):
    """Capture dispatch_task_notification calls made by the follow-up run."""
    sent: list[Task] = []

    async def _fake_dispatch(task, db, is_followup=False, **kwargs):
        assert is_followup is True
        sent.append(task)

    monkeypatch.setattr(daily_followup, "dispatch_task_notification", _fake_dispatch)
    return sent


async def _merge_notif_settings(db, **kv) -> None:
    """Merge keys (value None = drop the key) into notification_settings on
    EVERY config row — duplicate rows from earlier concurrent suites make
    single-row updates a coin flip."""
    cfg = await get_config(db)
    ns = {**(cfg.notification_settings or {})}
    for key, value in kv.items():
        if value is None:
            ns.pop(key, None)
        else:
            ns[key] = value
    await db.execute(sa_update(CompanyConfig).values(notification_settings=ns))
    await db.commit()


async def _set_daily_followup(db, enabled: bool | None) -> None:
    await _merge_notif_settings(db, daily_followup_enabled=enabled)


async def _make_open_task(db) -> Task:
    task = Task(
        type="approve_pr",
        document_type="pr",
        document_id=uuid.uuid4(),
        document_number="PR-FOLLOWUP-1",
        assigned_role="requester",
        title="Approve PR-FOLLOWUP-1",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def _delete_task(task_id: uuid.UUID) -> None:
    async with session_module.AsyncSessionLocal() as db:
        obj = await db.get(Task, task_id)
        if obj is not None:
            await db.delete(obj)
            await db.commit()


async def test_toggle_absent_skips_run(captured_dispatches):
    """Key missing from notification_settings (all pre-existing rows) = OFF."""
    async with session_module.AsyncSessionLocal() as db:
        await _set_daily_followup(db, None)
        task = await _make_open_task(db)
    try:
        await daily_followup.run_daily_followup()
        assert captured_dispatches == []
    finally:
        await _delete_task(task.id)


async def test_toggle_off_skips_run(captured_dispatches):
    async with session_module.AsyncSessionLocal() as db:
        await _set_daily_followup(db, False)
        task = await _make_open_task(db)
    try:
        await daily_followup.run_daily_followup()
        assert captured_dispatches == []
    finally:
        await _delete_task(task.id)


async def test_toggle_on_dispatches_open_tasks(captured_dispatches):
    async with session_module.AsyncSessionLocal() as db:
        await _set_daily_followup(db, True)
        task = await _make_open_task(db)
    try:
        await daily_followup.run_daily_followup()
        # Other suites may leave open tasks behind — assert on ours only.
        assert task.id in [t.id for t in captured_dispatches]
    finally:
        await _delete_task(task.id)


def test_default_notification_settings_daily_followup_off():
    assert _DEFAULT_NOTIFICATION_SETTINGS.get("daily_followup_enabled") is False


# ── followup_time drives the schedule ────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("14:30", (14, 30)),
    ("08:00", (8, 0)),
    ("0:05", (0, 5)),
    (" 23:59 ", (23, 59)),
])
def test_parse_followup_time_valid(raw, expected):
    assert daily_followup._parse_followup_time(raw) == expected


@pytest.mark.parametrize("raw", ["24:00", "12:60", "garbage", "12", "", None, 8, "-1:30"])
def test_parse_followup_time_invalid_falls_back_to_0800(raw):
    assert daily_followup._parse_followup_time(raw) == (8, 0)


async def test_load_schedule_reads_configured_time():
    async with session_module.AsyncSessionLocal() as db:
        await _merge_notif_settings(db, followup_time="14:30")
    assert await daily_followup._load_schedule() == (14, 30)


async def test_load_schedule_missing_key_defaults_to_0800():
    async with session_module.AsyncSessionLocal() as db:
        await _merge_notif_settings(db, followup_time=None)
    assert await daily_followup._load_schedule() == (8, 0)


async def test_loop_waits_toward_configured_time(monkeypatch):
    """One loop iteration sleeps min(recheck cap, time until the CONFIGURED
    HH:MM) — proving the loop reads followup_time instead of the old
    hardcoded 08:00."""
    async with session_module.AsyncSessionLocal() as db:
        await _merge_notif_settings(db, followup_time="14:30")

    waits: list[float] = []

    async def _capture_sleep(seconds):
        waits.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(daily_followup.asyncio, "sleep", _capture_sleep)
    expected = min(
        daily_followup._RECHECK_SECONDS,
        daily_followup._seconds_until_next_run(14, 30),
    )
    with pytest.raises(asyncio.CancelledError):
        await daily_followup.daily_followup_loop()
    assert waits and abs(waits[0] - expected) < 5
