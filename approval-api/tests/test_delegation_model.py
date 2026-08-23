import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.delegation import ApprovalDelegation


def _row(delegator, delegate, start, end):
    return ApprovalDelegation(
        id=uuid.uuid4(), delegator_user_id=delegator, delegate_user_id=delegate,
        start_date=start, end_date=end, created_by=uuid.uuid4())


@pytest.mark.asyncio
async def test_roundtrip(engine_db_session):
    db = engine_db_session
    a, b = uuid.uuid4(), uuid.uuid4()
    db.add(_row(a, b, date(2026, 8, 20), date(2026, 9, 3)))
    await db.flush()
    got = (await db.execute(sa.select(ApprovalDelegation))).scalars().one()
    assert got.delegator_user_id == a
    assert got.revoked_at is None


@pytest.mark.asyncio
async def test_cannot_delegate_to_self(engine_db_session):
    db = engine_db_session
    a = uuid.uuid4()
    db.add(_row(a, a, date(2026, 8, 20), date(2026, 9, 3)))
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_end_before_start_rejected(engine_db_session):
    db = engine_db_session
    db.add(_row(uuid.uuid4(), uuid.uuid4(), date(2026, 9, 3), date(2026, 8, 20)))
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_overlapping_live_windows_rejected(engine_db_session):
    db = engine_db_session
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add(_row(a, b, date(2026, 8, 20), date(2026, 9, 3)))
    await db.flush()
    db.add(_row(a, c, date(2026, 9, 1), date(2026, 9, 10)))
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_revoked_window_does_not_block_a_new_one(engine_db_session):
    """The exclusion constraint is partial: a revoked row must not reserve dates."""
    db = engine_db_session
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    first = _row(a, b, date(2026, 8, 20), date(2026, 9, 3))
    first.revoked_at = sa.func.now()
    db.add(first)
    await db.flush()
    db.add(_row(a, c, date(2026, 9, 1), date(2026, 9, 10)))
    await db.flush()   # must not raise
