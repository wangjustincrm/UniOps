"""One-shot backlog move: open process_pa tasks on ap_clerk must move to
payment_officer, completed tasks and other task types must be left alone,
a second run must be a no-op, dry-run must write nothing, and --revert must
restore the original state.

Modeled on tests/test_backfill_exception_tasks.py (script invoked as its
real entry point against the test DB). tasks.document_id carries no FK to
an actual PR/PO/PA/GR/invoice row (document_type is polymorphic), so a bare
Task row with a random UUID is sufficient — no PA needs to exist.
"""
import uuid
from decimal import Decimal

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
async def test_reassign_moves_open_ap_clerk_process_pa_task():
    async with sm.AsyncSessionLocal() as db:
        task = _make_task()
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        stats = await reassign(dry_run=False, db_url=_TEST_DB_URL)
        assert stats["changed"] >= 1

        async with sm.AsyncSessionLocal() as db:
            reloaded = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert reloaded.assigned_role == "payment_officer"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()


@pytest.mark.asyncio
async def test_reassign_leaves_completed_task_untouched():
    async with sm.AsyncSessionLocal() as db:
        task = _make_task(is_completed=True)
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        await reassign(dry_run=False, db_url=_TEST_DB_URL)

        async with sm.AsyncSessionLocal() as db:
            reloaded = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert reloaded.assigned_role == "ap_clerk", "completed tasks must never be reassigned"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()


@pytest.mark.asyncio
async def test_reassign_leaves_other_task_type_untouched():
    async with sm.AsyncSessionLocal() as db:
        task = _make_task(type_="resolve_exception")
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        await reassign(dry_run=False, db_url=_TEST_DB_URL)

        async with sm.AsyncSessionLocal() as db:
            reloaded = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert reloaded.assigned_role == "ap_clerk", "only process_pa tasks may move"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()


@pytest.mark.asyncio
async def test_reassign_is_idempotent():
    async with sm.AsyncSessionLocal() as db:
        task = _make_task()
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        first = await reassign(dry_run=False, db_url=_TEST_DB_URL)
        assert first["changed"] >= 1

        second = await reassign(dry_run=False, db_url=_TEST_DB_URL)
        assert second["changed"] == 0, "second run must find nothing left on ap_clerk"

        async with sm.AsyncSessionLocal() as db:
            reloaded = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert reloaded.assigned_role == "payment_officer", "must still be on the target role"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()


@pytest.mark.asyncio
async def test_reassign_dry_run_writes_nothing():
    async with sm.AsyncSessionLocal() as db:
        task = _make_task()
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        stats = await reassign(dry_run=True, db_url=_TEST_DB_URL)
        assert stats["changed"] >= 1, "dry-run must still report what it WOULD change"

        async with sm.AsyncSessionLocal() as db:
            reloaded = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert reloaded.assigned_role == "ap_clerk", "dry-run must not write"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()


@pytest.mark.asyncio
async def test_revert_restores_original_state():
    async with sm.AsyncSessionLocal() as db:
        task = _make_task()
        db.add(task)
        await db.flush()
        task_id = task.id
        await db.commit()

    try:
        await reassign(dry_run=False, db_url=_TEST_DB_URL)
        async with sm.AsyncSessionLocal() as db:
            moved = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert moved.assigned_role == "payment_officer"

        revert_stats = await reassign(dry_run=False, db_url=_TEST_DB_URL, revert=True)
        assert revert_stats["changed"] >= 1

        async with sm.AsyncSessionLocal() as db:
            restored = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
            assert restored.assigned_role == "ap_clerk", "revert must restore the original role"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id == task_id))
            await db.commit()
