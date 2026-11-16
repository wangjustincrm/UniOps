"""Procurement batch 15 — ISS-0172 follow-up: one open create_pa per PO
(INV-011, §3.14.2 Deduplication rule)."""
import pytest

from tests.test_invoice_allocations import _make_issued_po, _make_vendor


@pytest.fixture(autouse=True)
def _quiet_notification_email(monkeypatch):
    async def _noop(to, subject, html, **kwargs):
        return None

    monkeypatch.setattr("app.services.email.send_email", _noop)


def _create_pa_task(po, *, completed, description, created_at):
    import uuid as _uuid
    from datetime import datetime, timezone
    from app.models.task import Task
    return Task(type="create_pa", priority="normal", document_type="po",
                document_id=_uuid.UUID(po["id"]), document_number=po["number"],
                assigned_role="requester", title=f"Create Payment Application for {po['number']}",
                description=description, is_completed=completed,
                completed_at=datetime.now(timezone.utc) if completed else None,
                created_at=created_at)


@pytest.mark.asyncio
async def test_cancelled_pa_reopens_only_one_create_pa(admin_client, test_engine):
    """INV-011 / Deduplication rule: three earlier invoice → task → PA cycles
    left three completed prompts; the PA being cancelled used to reopen all
    three. Only the newest comes back, and none when one is already open."""
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.crud import pa as pa_crud
    from app.models.task import Task

    v = await _make_vendor(admin_client, "VND-B15-PA-01")
    po = await _make_issued_po(admin_client, v["id"])
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        for i in range(3):
            db.add(_create_pa_task(po, completed=True, description=f"Invoice INV-{i}",
                                   created_at=t0 + timedelta(minutes=i)))
        await db.commit()
        assert await pa_crud.reopen_create_pa_tasks_if_unpaid(db, _uuid.UUID(po["id"])) == 1
        await db.commit()
        open_rows = (await db.execute(select(Task).where(
            Task.type == "create_pa", Task.document_id == _uuid.UUID(po["id"]),
            Task.is_completed.is_(False)))).scalars().all()
        assert [t.description for t in open_rows] == ["Invoice INV-2"]
        # Already one open → a second release reopens nothing.
        assert await pa_crud.reopen_create_pa_tasks_if_unpaid(db, _uuid.UUID(po["id"])) == 0


@pytest.mark.asyncio
async def test_inbox_sweep_collapses_duplicate_create_pa(admin_client, test_engine):
    """Existing duplicates (PO-400-2611-15 had four open) are healed on the
    next inbox read: one stays, with PO-level wording."""
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.crud.task import _collapse_duplicate_create_pa_tasks
    from app.models.task import Task

    v = await _make_vendor(admin_client, "VND-B15-PA-02")
    po = await _make_issued_po(admin_client, v["id"])
    other = await _make_issued_po(admin_client, v["id"])
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        for i in range(4):
            db.add(_create_pa_task(po, completed=False, description=f"Invoice INV-{i}",
                                   created_at=t0 + timedelta(minutes=i)))
        db.add(_create_pa_task(other, completed=False, description="Invoice INV-X",
                               created_at=t0))
        await db.commit()
        await _collapse_duplicate_create_pa_tasks(db)
        await db.commit()
        rows = (await db.execute(select(Task).where(
            Task.type == "create_pa", Task.is_completed.is_(False),
            Task.document_id.in_([_uuid.UUID(po["id"]), _uuid.UUID(other["id"])])))).scalars().all()
        by_po = {str(t.document_id): t for t in rows}
        assert len(rows) == 2
        assert by_po[po["id"]].description == (
            f"Invoices matched to PO {po['number']} await a Payment Application.")
        assert by_po[other["id"]].description == "Invoice INV-X"
