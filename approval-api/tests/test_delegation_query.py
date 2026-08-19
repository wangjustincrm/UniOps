"""Boundary behaviour of the active-delegation predicate.

⚠️ SIBLING COPIES: epms-api/tests/test_delegation_query.py and
expense-api/tests/test_delegation_query.py assert the SAME four boundaries
against their own copy of this predicate. Change one, change all three.
"""
import uuid
from datetime import date

import pytest

from app.crud.delegation import active_delegate_id, active_delegator_ids
from app.models.delegation import ApprovalDelegation
from app.models.user import User

WINDOW_START = date(2026, 8, 20)
WINDOW_END = date(2026, 9, 3)


async def _seed(db, *, delegate_active=True):
    delegator = User(id=uuid.uuid4(), role="dept_manager", is_active=True)
    delegate = User(id=uuid.uuid4(), role="dept_manager", is_active=delegate_active)
    db.add_all([delegator, delegate])
    await db.flush()
    db.add(ApprovalDelegation(
        id=uuid.uuid4(), delegator_user_id=delegator.id,
        delegate_user_id=delegate.id, start_date=WINDOW_START,
        end_date=WINDOW_END, created_by=uuid.uuid4()))
    await db.flush()
    return delegator, delegate


@pytest.mark.asyncio
@pytest.mark.parametrize("today,expected", [
    (date(2026, 8, 19), False),   # day before start
    (WINDOW_START, True),         # first day, inclusive
    (WINDOW_END, True),           # last day, inclusive
    (date(2026, 9, 4), False),    # day after end
])
async def test_window_boundaries(engine_db_session, today, expected):
    db = engine_db_session
    delegator, delegate = await _seed(db)
    got = await active_delegator_ids(db, delegate.id, today=today)
    assert (delegator.id in got) is expected


@pytest.mark.asyncio
async def test_inactive_delegate_is_ignored(engine_db_session):
    """A deactivated stand-in must stop matching, so approval falls back to the
    delegator — who never lost the right. Nothing can strand."""
    db = engine_db_session
    delegator, delegate = await _seed(db, delegate_active=False)
    got = await active_delegator_ids(db, delegate.id, today=WINDOW_START)
    assert got == set()


@pytest.mark.asyncio
async def test_revoked_is_ignored_immediately(engine_db_session):
    db = engine_db_session
    delegator, delegate = await _seed(db)
    from sqlalchemy import text
    await db.execute(text(
        "UPDATE approval_delegations SET revoked_at = now() "
        "WHERE delegator_user_id = :d"), {"d": str(delegator.id)})
    got = await active_delegator_ids(db, delegate.id, today=WINDOW_START)
    assert got == set()


@pytest.mark.asyncio
async def test_not_transitive(engine_db_session):
    """A -> B and B -> C must not give C anything of A's."""
    db = engine_db_session
    a = User(id=uuid.uuid4(), role="dept_manager", is_active=True)
    b = User(id=uuid.uuid4(), role="dept_manager", is_active=True)
    c = User(id=uuid.uuid4(), role="dept_manager", is_active=True)
    db.add_all([a, b, c])
    await db.flush()
    db.add_all([
        ApprovalDelegation(id=uuid.uuid4(), delegator_user_id=a.id,
                           delegate_user_id=b.id, start_date=WINDOW_START,
                           end_date=WINDOW_END, created_by=uuid.uuid4()),
        ApprovalDelegation(id=uuid.uuid4(), delegator_user_id=b.id,
                           delegate_user_id=c.id, start_date=WINDOW_START,
                           end_date=WINDOW_END, created_by=uuid.uuid4()),
    ])
    await db.flush()
    got = await active_delegator_ids(db, c.id, today=WINDOW_START)
    assert got == {b.id}, "C acts for B only — never for A"


@pytest.mark.asyncio
async def test_reverse_lookup(engine_db_session):
    db = engine_db_session
    delegator, delegate = await _seed(db)
    assert await active_delegate_id(db, delegator.id, today=WINDOW_START) == delegate.id
    assert await active_delegate_id(db, delegator.id, today=date(2026, 9, 4)) is None
