"""Boundary behaviour of expense-api's read-only copy of the active-delegation
predicate.

⚠️ SIBLING COPIES: approval-api/tests/test_delegation_query.py (authoritative)
and epms-api/tests/test_delegation_query.py assert the SAME four boundaries
against their own copy of this predicate. Change one, change all three.

expense-api does not own `approval_delegations` (it is a shadow table created
in conftest.py — see test_engine), nor does it own `users` (also a shadow
table — expense-api has no ORM User model), so rows for both are inserted
with raw SQL, matching how app.core.delegation itself reads them.
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

TAG = uuid.uuid4().hex[:8]
WINDOW_START = date(2026, 8, 20)
WINDOW_END = date(2026, 9, 3)


async def _insert_user(db, *, role="dept_manager", is_active=True) -> uuid.UUID:
    uid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO users (id, full_name, email, role, is_active) "
        "VALUES (:id, 'Test User', :email, :role, :is_active)"),
        {"id": uid, "email": f"u-{uid.hex[:8]}-{TAG}@example.com",
         "role": role, "is_active": is_active})
    return uid


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
    delegator_id = await _insert_user(db)
    delegate_id = await _insert_user(db, is_active=delegate_active)
    await _insert_delegation(db, delegator_id=delegator_id, delegate_id=delegate_id)
    await db.flush()
    return delegator_id, delegate_id


@pytest.mark.asyncio
@pytest.mark.parametrize("today,expected", [
    (date(2026, 8, 19), False),   # day before start
    (WINDOW_START, True),         # first day, inclusive
    (WINDOW_END, True),           # last day, inclusive
    (date(2026, 9, 4), False),    # day after end
])
async def test_window_boundaries(test_engine, today, expected):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        delegator_id, delegate_id = await _seed(db)
        await db.commit()
        got = await active_delegator_ids(db, delegate_id, today=today)
        assert (delegator_id in got) is expected


@pytest.mark.asyncio
async def test_inactive_delegate_is_ignored(test_engine):
    """A deactivated stand-in must stop matching, so approval falls back to
    the delegator — who never lost the right. Nothing can strand."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        delegator_id, delegate_id = await _seed(db, delegate_active=False)
        await db.commit()
        got = await active_delegator_ids(db, delegate_id, today=WINDOW_START)
        assert got == set()


@pytest.mark.asyncio
async def test_revoked_is_ignored_immediately(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        delegator_id, delegate_id = await _seed(db)
        await db.commit()
        await db.execute(text(
            "UPDATE approval_delegations SET revoked_at = now() "
            "WHERE delegator_user_id = :d"), {"d": str(delegator_id)})
        got = await active_delegator_ids(db, delegate_id, today=WINDOW_START)
        assert got == set()


@pytest.mark.asyncio
async def test_not_transitive(test_engine):
    """A -> B and B -> C must not give C anything of A's."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        a = await _insert_user(db)
        b = await _insert_user(db)
        c = await _insert_user(db)
        await _insert_delegation(db, delegator_id=a, delegate_id=b)
        await _insert_delegation(db, delegator_id=b, delegate_id=c)
        await db.flush()
        got = await active_delegator_ids(db, c, today=WINDOW_START)
        assert got == {b}, "C acts for B only — never for A"


@pytest.mark.asyncio
async def test_reverse_lookup(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        delegator_id, delegate_id = await _seed(db)
        await db.commit()
        assert await active_delegate_id(db, delegator_id, today=WINDOW_START) == delegate_id
        assert await active_delegate_id(db, delegator_id, today=date(2026, 9, 4)) is None


@pytest.mark.asyncio
async def test_broadcast_roles_union_minus_personal(test_engine):
    """Union of delegators' role codes (users.role + shadow user_roles),
    minus the personal-routed roles (director/supervisor)."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        d1 = await _insert_user(db, role="finance_bp")
        d2 = await _insert_user(db, role="director")  # personal role — must be excluded
        await db.execute(text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (:u, :r)"),
            {"u": d1, "r": "procurement_manager"})
        await db.commit()
        got = await delegated_broadcast_roles(db, {d1, d2})
        assert got == {"finance_bp", "procurement_manager"}


@pytest.mark.asyncio
async def test_broadcast_roles_empty_input(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        assert await delegated_broadcast_roles(db, set()) == set()


@pytest.mark.asyncio
async def test_broadcast_roles_excludes_inactive_delegator_additional_role(test_engine):
    """A deactivated delegator's ADDITIONAL role (user_roles) must not leak
    into the broadcast set, matching how their PRIMARY role (users.role) is
    already excluded once inactive. Both halves of the union filter
    is_active identically."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        delegator_id = await _insert_user(db, role="dept_manager", is_active=False)
        await db.execute(text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (:u, :r)"),
            {"u": delegator_id, "r": "procurement_manager"})
        await db.commit()
        got = await delegated_broadcast_roles(db, {delegator_id})
        assert got == set()
