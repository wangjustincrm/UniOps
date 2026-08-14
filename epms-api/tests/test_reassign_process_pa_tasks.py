"""One-shot backlog move: open process_pa tasks on ap_clerk must move to
payment_officer, completed tasks and other task types must be left alone,
a second run must be a no-op, dry-run must write nothing, and --revert must
restore exactly what the forward run moved — never a task approval-api has
since assigned directly to payment_officer.

Modeled on tests/test_backfill_exception_tasks.py (script invoked as its
real entry point against the test DB). tasks.document_id carries no FK to
an actual PR/PO/PA/GR/invoice row (document_type is polymorphic), so a bare
Task row with a random UUID is sufficient — no PA needs to exist.

Every test passes its own tmp_path ids-file so runs never touch (or race on)
scripts/reassign_process_pa_tasks.py's real default moved-ids file.
"""
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

import app.db.session as sm
from app.models.task import Task
from scripts.reassign_process_pa_tasks import reassign

from tests.conftest import _TEST_DB_URL


def _make_task(*, type_: str = "process_pa", assigned_role: str = "ap_clerk",
                is_completed: bool = False, ref: str | None = None) -> Task:
    ref = ref or f"PA-TEST-{uuid.uuid4().hex[:8]}"
    return Task(
        type=type_, priority="normal", document_type="pa",
        document_id=uuid.uuid4(), document_number=ref,
        assigned_role=assigned_role, title=f"Process payment for {ref}",
        amount=Decimal("100.00"), vendor="Acme",
        is_completed=is_completed,
    )


@pytest.mark.asyncio
async def test_reassign_moves_open_ap_clerk_process_pa_task(tmp_path: Path):
    ids_file = tmp_path / "moved.json"
    async with sm.AsyncSessionLocal() as db:
        task = _make_task()
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        stats = await reassign(dry_run=False, db_url=_TEST_DB_URL, ids_file=ids_file)
        assert stats["changed"] >= 1
        assert str(task_id) in stats["changed_ids"]
        assert ids_file.exists(), "forward apply must record the moved-ids file"

        async with sm.AsyncSessionLocal() as db:
            reloaded = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert reloaded.assigned_role == "payment_officer"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()


@pytest.mark.asyncio
async def test_reassign_leaves_completed_task_untouched(tmp_path: Path):
    ids_file = tmp_path / "moved.json"
    async with sm.AsyncSessionLocal() as db:
        task = _make_task(is_completed=True)
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        await reassign(dry_run=False, db_url=_TEST_DB_URL, ids_file=ids_file)

        async with sm.AsyncSessionLocal() as db:
            reloaded = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert reloaded.assigned_role == "ap_clerk", "completed tasks must never be reassigned"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()


@pytest.mark.asyncio
async def test_reassign_leaves_other_task_type_untouched(tmp_path: Path):
    ids_file = tmp_path / "moved.json"
    async with sm.AsyncSessionLocal() as db:
        task = _make_task(type_="resolve_exception")
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        await reassign(dry_run=False, db_url=_TEST_DB_URL, ids_file=ids_file)

        async with sm.AsyncSessionLocal() as db:
            reloaded = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert reloaded.assigned_role == "ap_clerk", "only process_pa tasks may move"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()


@pytest.mark.asyncio
async def test_reassign_is_idempotent(tmp_path: Path):
    ids_file = tmp_path / "moved.json"
    async with sm.AsyncSessionLocal() as db:
        task = _make_task()
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        first = await reassign(dry_run=False, db_url=_TEST_DB_URL, ids_file=ids_file)
        assert first["changed"] >= 1

        second = await reassign(dry_run=False, db_url=_TEST_DB_URL, ids_file=ids_file)
        assert second["changed"] == 0, "second run must find nothing left on ap_clerk"

        async with sm.AsyncSessionLocal() as db:
            reloaded = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert reloaded.assigned_role == "payment_officer", "must still be on the target role"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()


@pytest.mark.asyncio
async def test_reassign_dry_run_writes_nothing(tmp_path: Path):
    ids_file = tmp_path / "moved.json"
    async with sm.AsyncSessionLocal() as db:
        task = _make_task()
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        stats = await reassign(dry_run=True, db_url=_TEST_DB_URL, ids_file=ids_file)
        assert stats["changed"] >= 1, "dry-run must still report what it WOULD change"
        assert not ids_file.exists(), "dry-run must not write the moved-ids file either"

        async with sm.AsyncSessionLocal() as db:
            reloaded = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert reloaded.assigned_role == "ap_clerk", "dry-run must not write"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()


@pytest.mark.asyncio
async def test_revert_restores_original_state(tmp_path: Path):
    ids_file = tmp_path / "moved.json"
    async with sm.AsyncSessionLocal() as db:
        task = _make_task()
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        await reassign(dry_run=False, db_url=_TEST_DB_URL, ids_file=ids_file)
        async with sm.AsyncSessionLocal() as db:
            moved = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert moved.assigned_role == "payment_officer"

        revert_stats = await reassign(dry_run=False, db_url=_TEST_DB_URL, revert=True, ids_file=ids_file)
        assert revert_stats["changed"] >= 1

        async with sm.AsyncSessionLocal() as db:
            restored = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert restored.assigned_role == "ap_clerk", "revert must restore the original role"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()


@pytest.mark.asyncio
async def test_revert_leaves_task_never_moved_by_this_script_alone(tmp_path: Path):
    """The scenario the pre-fix suite could not distinguish: a process_pa
    task assigned directly to payment_officer (as approval-api does for new
    PAs post-release) that this script's forward run never touched. A
    --revert of an unrelated forward run must not sweep it back to
    ap_clerk — that is exactly the corruption the moved-ids scoping exists
    to prevent.
    """
    ids_file = tmp_path / "moved.json"
    async with sm.AsyncSessionLocal() as db:
        moved_task = _make_task(assigned_role="ap_clerk")
        untouched_task = _make_task(assigned_role="payment_officer")
        db.add_all([moved_task, untouched_task])
        await db.flush()
        moved_id, untouched_id = moved_task.id, untouched_task.id
        await db.commit()

    try:
        fwd = await reassign(dry_run=False, db_url=_TEST_DB_URL, ids_file=ids_file)
        assert str(moved_id) in fwd["changed_ids"]
        assert str(untouched_id) not in fwd["changed_ids"]

        revert_stats = await reassign(dry_run=False, db_url=_TEST_DB_URL, revert=True, ids_file=ids_file)
        assert str(moved_id) in revert_stats["changed_ids"]
        assert str(untouched_id) not in revert_stats["changed_ids"]

        async with sm.AsyncSessionLocal() as db:
            reverted = (await db.execute(select(Task).where(Task.id == moved_id))).scalar_one()
            still_there = (await db.execute(select(Task).where(Task.id == untouched_id))).scalar_one()
            assert reverted.assigned_role == "ap_clerk"
            assert still_there.assigned_role == "payment_officer", (
                "a task this script never moved must survive --revert untouched"
            )
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id.in_([moved_id, untouched_id])))
            await db.commit()


@pytest.mark.asyncio
async def test_revert_without_ids_file_refuses_and_writes_nothing(tmp_path: Path):
    ids_file = tmp_path / "does-not-exist.json"
    async with sm.AsyncSessionLocal() as db:
        task = _make_task(assigned_role="payment_officer")
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        with pytest.raises(SystemExit):
            await reassign(dry_run=False, db_url=_TEST_DB_URL, revert=True, ids_file=ids_file)
        assert not ids_file.exists(), "a refused revert must not create the ids file"

        async with sm.AsyncSessionLocal() as db:
            reloaded = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert reloaded.assigned_role == "payment_officer", "refusal must write nothing to the DB either"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()
