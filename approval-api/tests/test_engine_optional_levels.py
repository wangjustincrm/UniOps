"""Tests for optional approval-level resolvers: _resolve_director and _resolve_supervisor.

These helpers are pure DB queries with no side effects; they are wired into task
creation/authorization/skip in later tasks.  This file covers only the resolution
logic + active-user validation.
"""
import uuid

import pytest

from app.crud.engine import _resolve_director, _resolve_supervisor
from app.models.user import User


@pytest.mark.asyncio
async def test_resolve_director_returns_active_mapped_user(engine_db_session):
    db = engine_db_session
    dept = uuid.uuid4()
    director = User(id=uuid.uuid4(), role="requester", department_id=None, is_active=True)
    requester = User(id=uuid.uuid4(), role="requester", department_id=dept, is_active=True)
    db.add_all([director, requester])
    await db.flush()
    mapping = {str(dept): str(director.id)}
    assert await _resolve_director(db, requester.id, mapping) == director.id


@pytest.mark.asyncio
async def test_resolve_director_none_when_unmapped(engine_db_session):
    db = engine_db_session
    dept = uuid.uuid4()
    requester = User(id=uuid.uuid4(), role="requester", department_id=dept, is_active=True)
    db.add(requester)
    await db.flush()
    assert await _resolve_director(db, requester.id, {}) is None


@pytest.mark.asyncio
async def test_resolve_director_none_when_mapped_user_inactive(engine_db_session):
    db = engine_db_session
    dept = uuid.uuid4()
    director = User(id=uuid.uuid4(), role="requester", is_active=False)
    requester = User(id=uuid.uuid4(), role="requester", department_id=dept, is_active=True)
    db.add_all([director, requester])
    await db.flush()
    assert await _resolve_director(db, requester.id, {str(dept): str(director.id)}) is None


@pytest.mark.asyncio
async def test_resolve_supervisor_requires_enabled_dept_and_active_user(engine_db_session):
    db = engine_db_session
    dept = uuid.uuid4()
    sup = User(id=uuid.uuid4(), role="requester", is_active=True)
    req = User(
        id=uuid.uuid4(),
        role="requester",
        department_id=dept,
        is_active=True,
        supervisor_id=sup.id,
    )
    db.add_all([sup, req])
    await db.flush()
    assert await _resolve_supervisor(db, req.id, {str(dept): True}) == sup.id
    assert await _resolve_supervisor(db, req.id, {str(dept): False}) is None
    assert await _resolve_supervisor(db, req.id, {}) is None
