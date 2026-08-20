"""Boundary behaviour of epms-api's read-only copy of the active-delegation
predicate.

⚠️ SIBLING COPIES: approval-api/tests/test_delegation_query.py (authoritative)
and expense-api/tests/test_delegation_query.py assert the SAME four
boundaries against their own copy of this predicate. Change one, change all
three.

epms-api does not own `approval_delegations` (it is a shadow table created in
conftest.py — see test_engine), so there is no ORM model here; rows are
inserted with raw SQL instead of an ORM object, matching how app.core.delegation
itself reads the table.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.delegation import (
    active_delegate_id,
    active_delegator_ids,
    delegated_broadcast_roles,
)
from app.core.security import hash_password
from app.models.user import User

TAG = uuid.uuid4().hex[:8]
WINDOW_START = date(2026, 8, 20)
WINDOW_END = date(2026, 9, 3)


def _user(*, role="dept_manager", is_active=True):
    uid = uuid.uuid4()
    return User(
        id=uid, email=f"u-{uid.hex[:8]}-{TAG}@example.com",
        hashed_password=hash_password("x"), full_name="Test User",
        role=role, is_active=is_active,
    )


async def _insert_delegation(db, *, delegator_id, delegate_id,
                              start_date=WINDOW_START, end_date=WINDOW_END,
                              revoked_at=None):
    await db.execute(text(
        "INSERT INTO approval_delegations "
        "(id, delegator_user_id, delegate_user_id, start_date, end_date, "
        " revoked_at, created_by) "
        "VALUES (:id, :delegator_id, :delegate_id, :start_date, :end_date, "
        " :revoked_at, :created_by)"),
        {
            "id": uuid.uuid4(), "delegator_id": delegator_id,
            "delegate_id": delegate_id, "start_date": start_date,
            "end_date": end_date, "revoked_at": revoked_at,
            "created_by": uuid.uuid4(),
        })


async def _seed(db, *, delegate_active=True):
    delegator = _user()
    delegate = _user(is_active=delegate_active)
    db.add_all([delegator, delegate])
    await db.flush()
    await _insert_delegation(db, delegator_id=delegator.id, delegate_id=delegate.id)
    await db.flush()
    return delegator, delegate


@pytest.mark.parametrize("today,expected", [
    (date(2026, 8, 19), False),   # day before start
    (WINDOW_START, True),         # first day, inclusive
    (WINDOW_END, True),           # last day, inclusive
    (date(2026, 9, 4), False),    # day after end
])
async def test_window_boundaries(test_engine, today, expected):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        delegator, delegate = await _seed(db)
        await db.commit()
        got = await active_delegator_ids(db, delegate.id, today=today)
        assert (delegator.id in got) is expected


async def test_inactive_delegate_is_ignored(test_engine):
    """A deactivated stand-in must stop matching, so approval falls back to
    the delegator — who never lost the right. Nothing can strand."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        delegator, delegate = await _seed(db, delegate_active=False)
        await db.commit()
        got = await active_delegator_ids(db, delegate.id, today=WINDOW_START)
        assert got == set()


async def test_revoked_is_ignored_immediately(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        delegator, delegate = await _seed(db)
        await db.commit()
        await db.execute(text(
            "UPDATE approval_delegations SET revoked_at = now() "
            "WHERE delegator_user_id = :d"), {"d": str(delegator.id)})
        got = await active_delegator_ids(db, delegate.id, today=WINDOW_START)
        assert got == set()


async def test_not_transitive(test_engine):
    """A -> B and B -> C must not give C anything of A's."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        a, b, c = _user(), _user(), _user()
        db.add_all([a, b, c])
        await db.flush()
        await _insert_delegation(db, delegator_id=a.id, delegate_id=b.id)
        await _insert_delegation(db, delegator_id=b.id, delegate_id=c.id)
        await db.flush()
        got = await active_delegator_ids(db, c.id, today=WINDOW_START)
        assert got == {b.id}, "C acts for B only — never for A"


async def test_reverse_lookup(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        delegator, delegate = await _seed(db)
        await db.commit()
        assert await active_delegate_id(db, delegator.id, today=WINDOW_START) == delegate.id
        assert await active_delegate_id(db, delegator.id, today=date(2026, 9, 4)) is None


async def test_broadcast_roles_union_minus_personal(test_engine):
    """Union of delegators' role codes (users.role + shadow user_roles),
    minus the personal-routed roles (director/supervisor)."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        d1 = _user(role="finance_bp")
        d2 = _user(role="director")  # personal role — must be excluded
        db.add_all([d1, d2])
        await db.flush()
        await db.execute(text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (:u, :r)"),
            {"u": d1.id, "r": "procurement_manager"})
        await db.commit()
        got = await delegated_broadcast_roles(db, {d1.id, d2.id})
        assert got == {"finance_bp", "procurement_manager"}


async def test_broadcast_roles_empty_input(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        assert await delegated_broadcast_roles(db, set()) == set()
