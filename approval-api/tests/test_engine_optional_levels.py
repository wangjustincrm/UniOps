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
    assert await _resolve_director(db, dept, mapping) == director.id


@pytest.mark.asyncio
async def test_resolve_director_none_when_unmapped(engine_db_session):
    db = engine_db_session
    dept = uuid.uuid4()
    requester = User(id=uuid.uuid4(), role="requester", department_id=dept, is_active=True)
    db.add(requester)
    await db.flush()
    assert await _resolve_director(db, dept, {}) is None


@pytest.mark.asyncio
async def test_resolve_director_none_when_mapped_user_inactive(engine_db_session):
    db = engine_db_session
    dept = uuid.uuid4()
    director = User(id=uuid.uuid4(), role="requester", is_active=False)
    requester = User(id=uuid.uuid4(), role="requester", department_id=dept, is_active=True)
    db.add_all([director, requester])
    await db.flush()
    assert await _resolve_director(db, dept, {str(dept): str(director.id)}) is None


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


# ── Routed / authorized / skippable supervisor & director steps (Task 6+7) ──────
#
# The PR default workflow is [supervisor, dept_manager, director, gm_or_opm].
# These tests seed a full CompanyConfig (role_management + dept_gm_opm_mapping)
# plus optional director / supervisor configuration and drive real submit/approve
# actions through execute_action to verify assignment, skipping and authorization.

from decimal import Decimal

from sqlalchemy import select

from app.crud.engine import execute_action
from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.pr import PurchaseRequest
from app.models.routing import DeptRouting
from app.models.task import Task


async def _seed(
    db,
    *,
    with_director=False,
    director_active=True,
    with_supervisor=False,
):
    """Seed requester (with dept), a dept_manager, a gm user + dept routing row, and
    a draft PR. Optionally add a director and/or supervisor.

    role_management/dept_gm_opm_mapping/dept_director_mapping/dept_supervisor_enabled
    used to be seeded as company_config JSONB (retired mirror, Task 4) — this test
    exercises engine BEHAVIOUR, not the data source, so the GM post is now the gm
    user's own primary role (users.role) and the dept-level routing facts live on
    an approval_dept_routing row instead.

    Returns a dict of the created objects.
    """
    dept = uuid.uuid4()
    gm = User(id=uuid.uuid4(), role="gm", is_active=True)
    manager = User(id=uuid.uuid4(), role="dept_manager", department_id=dept, is_active=True)

    supervisor = None
    supervisor_id = None
    if with_supervisor:
        supervisor = User(id=uuid.uuid4(), role="requester", is_active=True)
        supervisor_id = supervisor.id

    requester = User(
        id=uuid.uuid4(),
        role="requester",
        department_id=dept,
        is_active=True,
        supervisor_id=supervisor_id,
    )

    director = None
    director_user_id = None
    if with_director:
        director = User(id=uuid.uuid4(), role="requester", is_active=director_active)
        director_user_id = director.id

    to_add = [gm, manager, requester]
    if supervisor is not None:
        to_add.append(supervisor)
    if director is not None:
        to_add.append(director)
    db.add_all(to_add)
    await db.flush()

    cfg = CompanyConfig(id=uuid.uuid4(), workflow_defs={})
    db.add(cfg)
    db.add(DeptRouting(
        dept_id=dept,
        gm_or_opm="gm",
        director_user_id=director_user_id,
        supervisor_enabled=with_supervisor,
    ))
    await db.flush()

    pr = PurchaseRequest(
        number=f"PR-OPT-{uuid.uuid4().hex[:4]}",
        title="Optional-levels engine test",
        status="draft",
        approval_step_idx=0,
        amount=Decimal("100.00"),
        created_by=requester.id,
    )
    db.add(pr)
    await db.flush()

    return {
        "dept": dept,
        "gm": gm,
        "manager": manager,
        "requester": requester,
        "supervisor": supervisor,
        "director": director,
        "pr": pr,
    }


async def _open_approve_task(db, pr_id):
    return (await db.execute(
        select(Task).where(
            Task.document_id == pr_id,
            Task.type == "approve_pr",
            Task.is_completed.is_(False),
        )
    )).scalar_one()


async def _skip_events(db, pr_id, role):
    rows = (await db.execute(
        select(ApprovalEvent).where(
            ApprovalEvent.document_id == pr_id,
            ApprovalEvent.actor_role == role,
            ApprovalEvent.action == "approve",
        )
    )).scalars().all()
    return rows


@pytest.mark.asyncio
async def test_director_active_task_assigned(engine_db_session):
    """Director active: submit → approve as manager → open task is director,
    assigned to the director user. (No supervisor configured → step 0 skipped
    at submit, landing on dept_manager.)"""
    db = engine_db_session
    s = await _seed(db, with_director=True)
    pr = s["pr"]

    await execute_action(db, "pr", pr.id, "submit", s["requester"].id, "requester")
    # supervisor auto-skipped at submit → open task is dept_manager
    task = await _open_approve_task(db, pr.id)
    assert task.assigned_role == "dept_manager"

    await execute_action(db, "pr", pr.id, "approve", s["manager"].id, "dept_manager")
    task = await _open_approve_task(db, pr.id)
    assert task.assigned_role == "director"
    assert task.assigned_user_id == s["director"].id


@pytest.mark.asyncio
async def test_director_skipped_when_unmapped(engine_db_session):
    """Director unmapped for dept: submit → approve as manager → open task is
    gm_or_opm; a skip ApprovalEvent exists for the director step."""
    db = engine_db_session
    s = await _seed(db, with_director=False)
    pr = s["pr"]

    await execute_action(db, "pr", pr.id, "submit", s["requester"].id, "requester")
    await execute_action(db, "pr", pr.id, "approve", s["manager"].id, "dept_manager")

    # gm_or_opm step resolves to the concrete "gm" user for this dept mapping
    task = await _open_approve_task(db, pr.id)
    assert task.assigned_role == "gm"
    assert task.assigned_user_id == s["gm"].id

    events = await _skip_events(db, pr.id, "director")
    assert len(events) == 1
    assert "Director" in (events[0].comment or "")


@pytest.mark.asyncio
async def test_supervisor_active_first_task(engine_db_session):
    """Supervisor active: submit → first open task is supervisor, assigned to the
    supervisor user."""
    db = engine_db_session
    s = await _seed(db, with_supervisor=True)
    pr = s["pr"]

    await execute_action(db, "pr", pr.id, "submit", s["requester"].id, "requester")
    task = await _open_approve_task(db, pr.id)
    assert task.assigned_role == "supervisor"
    assert task.assigned_user_id == s["supervisor"].id


@pytest.mark.asyncio
async def test_supervisor_skipped_at_submit(engine_db_session):
    """Supervisor level not enabled for dept: submit → first open task is
    dept_manager; a skip event is recorded for the supervisor step."""
    db = engine_db_session
    s = await _seed(db, with_supervisor=False)
    pr = s["pr"]

    await execute_action(db, "pr", pr.id, "submit", s["requester"].id, "requester")
    task = await _open_approve_task(db, pr.id)
    assert task.assigned_role == "dept_manager"

    events = await _skip_events(db, pr.id, "supervisor")
    assert len(events) == 1


@pytest.mark.asyncio
async def test_inactive_director_skipped_with_reason(engine_db_session):
    """Director mapped but inactive: after manager approve, open task is gm_or_opm;
    skip event comment flags the configured Director as inactive."""
    db = engine_db_session
    s = await _seed(db, with_director=True, director_active=False)
    pr = s["pr"]

    await execute_action(db, "pr", pr.id, "submit", s["requester"].id, "requester")
    await execute_action(db, "pr", pr.id, "approve", s["manager"].id, "dept_manager")

    # gm_or_opm step resolves to the concrete "gm" user for this dept mapping
    task = await _open_approve_task(db, pr.id)
    assert task.assigned_role == "gm"
    assert task.assigned_user_id == s["gm"].id

    events = await _skip_events(db, pr.id, "director")
    assert len(events) == 1
    assert "configured Director is inactive" in (events[0].comment or "")


@pytest.mark.asyncio
async def test_random_user_cannot_approve_director_step(engine_db_session):
    """Authorization: a random user must not be able to approve the director step."""
    db = engine_db_session
    s = await _seed(db, with_director=True)
    pr = s["pr"]

    await execute_action(db, "pr", pr.id, "submit", s["requester"].id, "requester")
    await execute_action(db, "pr", pr.id, "approve", s["manager"].id, "dept_manager")
    # now on the director step
    intruder = User(id=uuid.uuid4(), role="requester", is_active=True)
    db.add(intruder)
    await db.flush()

    with pytest.raises(ValueError, match="Not authorized"):
        await execute_action(db, "pr", pr.id, "approve", intruder.id, "requester")
