"""The sync lock must not be able to outlive the work it protects.

Reported 2026-08-19: the Inventory page showed "Syncing from WMS…" with a
spinner that never stopped, long after the sync had finished successfully. The
status endpoint's `running` flag was stuck true because the advisory lock had
been stranded on a pooled connection — the lock was taken through a SQLAlchemy
Session, and the `pg_advisory_unlock` in the `finally` ran on a DIFFERENT
connection than the `pg_try_advisory_lock` had (a Session does not promise to
keep one connection across the commit `run_wms_sync` does mid-way). Unlocking
a lock you do not hold returns false and logs nothing.

These tests pin the property that makes that impossible: the lock is
TRANSACTION-scoped, so Postgres ends it when the transaction ends — there is
no unlock statement left to miss, and no connection left holding anything.

The first test fails against the old session-scoped implementation, which is
the point: it is the regression, not a restatement of the fix.
"""
import pytest
from sqlalchemy import text

from app.services.wms_sync.lock import ADVISORY_LOCK_KEY, is_sync_running, wms_sync_lock


async def _held_elsewhere(db_engine) -> bool:
    """Ask a connection of our own whether anybody holds the lock."""
    async with db_engine.connect() as conn:
        held = (await conn.execute(text(
            "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
            "AND ((classid::bigint << 32) | objid::bigint) = :k LIMIT 1"
        ), {"k": ADVISORY_LOCK_KEY})).scalar()
    return held is not None


@pytest.mark.anyio
async def test_the_lock_ends_with_the_transaction(db_session, db_engine):
    """`run_wms_sync` commits in the middle of the locked section. After that
    commit the lock must be gone — held by nothing, on any connection.

    Under the old session-scoped lock this failed: the commit ended the
    transaction but the lock stayed on whatever connection had taken it, and
    the later unlock could (and in production did) land somewhere else."""
    async with wms_sync_lock(db_session) as got:
        assert got is True
        assert await _held_elsewhere(db_engine) is True   # held during the work
        await db_session.commit()                          # what run_wms_sync does
        assert await _held_elsewhere(db_engine) is False   # ...and released by it


@pytest.mark.anyio
async def test_nothing_is_left_holding_the_lock_afterwards(db_session, db_engine):
    """Whatever happens inside, leaving the block leaves the lock free."""
    async with wms_sync_lock(db_session) as got:
        assert got is True
    await db_session.rollback()
    assert await _held_elsewhere(db_engine) is False


@pytest.mark.anyio
async def test_a_failure_inside_the_section_still_frees_the_lock(db_session, db_engine):
    """A stranded lock would make every later sync 409 and the page spin
    forever, so the failure path matters more than the happy one."""
    with pytest.raises(RuntimeError):
        async with wms_sync_lock(db_session) as got:
            assert got is True
            raise RuntimeError("ORA-12541: TNS:no listener")
    await db_session.rollback()
    assert await _held_elsewhere(db_engine) is False


@pytest.mark.anyio
async def test_a_second_holder_is_refused_while_the_first_is_working(db_session, db_engine):
    """Single flight still holds: the whole reason for the lock is that
    run_wms_sync empties the mirror before refilling it."""
    async with wms_sync_lock(db_session) as first:
        assert first is True
        async with db_engine.connect() as other:
            second = (await other.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY}
            )).scalar_one()
            assert second is False
            await other.rollback()


@pytest.mark.anyio
async def test_is_sync_running_does_not_take_the_lock(db_session, db_engine):
    """The status endpoint calls this on every poll; if asking took the lock,
    reading the page would block the thing it is reporting on."""
    assert await is_sync_running(db_session) is False
    assert await _held_elsewhere(db_engine) is False
