"""vms_visit through the engine: return → resubmit / cancel, and task titles.

A returned visit used to be a dead end: `valid_submit` held only `draft` and
`valid_cancel` left out `returned`, so the creator could neither resubmit it nor
cancel it in the engine — the visit sat in Pending Approval and its revise task
never closed (VMS PRD V2.7 §12 D-03).
"""
import uuid
from datetime import date

import pytest

from app.crud.engine import execute_action
from app.models.config import CompanyConfig
from app.models.task import Task
from app.models.user import User
from app.models.visit import Visit as VmsVisit
from sqlalchemy import select

# One step, resolved from the visit itself — no department routing involved.
# Still the pre-2026-10 role name: workflows saved before zones keep working.
_WORKFLOW = [{"id": "quality_manager", "role": "quality_manager", "label": "Quality Manager"}]
_ZONE_WORKFLOW = [{"id": "zone_manager", "role": "zone_manager", "label": "Zone Manager"}]


async def _setup(db):
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"vms_visit": _WORKFLOW},
                         budget_admin_config={}))
    owner = User(id=uuid.uuid4(), full_name="Owner", role="requester", is_active=True)
    qm = User(id=uuid.uuid4(), full_name="QM", role="requester", is_active=True)
    db.add_all([owner, qm])
    await db.flush()
    visit = VmsVisit(
        id=uuid.uuid4(), visitor_id=uuid.uuid4(), host_id=owner.id, created_by=owner.id,
        access_area="production_gmp", visit_date=date(2026, 10, 2),
        visit_title="VMS Visit — Ada Lovelace (Acme)", zone_approver_ids=[str(qm.id)],
        approval_status="draft",
    )
    db.add(visit)
    await db.flush()
    return visit, owner, qm


async def _open_tasks(db, visit_id):
    return (await db.execute(select(Task).where(
        Task.document_id == visit_id, Task.is_completed.is_(False)))).scalars().all()


async def test_returned_visit_can_be_resubmitted(engine_db_session):
    db = engine_db_session
    visit, owner, qm = await _setup(db)
    await execute_action(db, "vms_visit", visit.id, "submit", owner.id, "requester")
    await execute_action(db, "vms_visit", visit.id, "return", qm.id, "requester", comment="wrong date")
    await db.refresh(visit)
    assert visit.approval_status == "returned"

    result = await execute_action(db, "vms_visit", visit.id, "submit", owner.id, "requester")
    assert result.new_status == "submitted"
    approve = [t for t in await _open_tasks(db, visit.id) if t.type == "approve_vms_visit"]
    assert len(approve) == 1 and approve[0].assigned_user_id == qm.id


async def test_returned_visit_can_be_cancelled_and_revise_task_closes(engine_db_session):
    db = engine_db_session
    visit, owner, qm = await _setup(db)
    await execute_action(db, "vms_visit", visit.id, "submit", owner.id, "requester")
    await execute_action(db, "vms_visit", visit.id, "return", qm.id, "requester")
    assert [t.type for t in await _open_tasks(db, visit.id)] == ["revise_vms_visit"]

    result = await execute_action(db, "vms_visit", visit.id, "cancel", owner.id, "requester")
    assert result.new_status == "cancelled"
    assert await _open_tasks(db, visit.id) == []


async def test_task_title_names_visitor_once_plus_area_and_date(engine_db_session):
    db = engine_db_session
    visit, owner, _qm = await _setup(db)
    await execute_action(db, "vms_visit", visit.id, "submit", owner.id, "requester")
    (task,) = await _open_tasks(db, visit.id)
    assert task.title == (
        "Approve visitor visit: VMS Visit — Ada Lovelace (Acme) — Production (GMP), 2026-10-02"
    )
    assert task.title.count("Ada Lovelace") == 1


async def test_first_submit_with_null_step_index(engine_db_session):
    """vms_visits.approval_step_idx is NULL until the engine first writes it.
    Since e9f39159 hoisted `step < len(workflow)` above the action branch, that
    NULL raised TypeError on every VMS submit."""
    db = engine_db_session
    visit, owner, qm = await _setup(db)
    assert visit.approval_step_idx is None
    result = await execute_action(db, "vms_visit", visit.id, "submit", owner.id, "requester")
    assert result.new_status == "submitted"
    assert result.approval_step_idx == 0


# ── Zone Manager step: one task per zone manager, first to act decides ───────

async def _zone_setup(db, *, managers: int, workflow=_ZONE_WORKFLOW, zone_name=None):
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"vms_visit": workflow},
                         budget_admin_config={}))
    owner = User(id=uuid.uuid4(), full_name="Owner", role="requester", is_active=True)
    mgrs = [User(id=uuid.uuid4(), full_name=f"M{i}", role="requester", is_active=True)
            for i in range(managers)]
    db.add_all([owner, *mgrs])
    code = f"cold_room_{uuid.uuid4().hex[:6]}"
    if zone_name:
        from app.models.visit import VmsZone
        db.add(VmsZone(id=uuid.uuid4(), code=code, name=zone_name))
    await db.flush()
    visit = VmsVisit(
        id=uuid.uuid4(), visitor_id=uuid.uuid4(), host_id=owner.id, created_by=owner.id,
        access_area=code, visit_date=date(2026, 10, 2),
        visit_title="VMS Visit — Ada Lovelace (Acme)",
        zone_approver_ids=[str(m.id) for m in mgrs], approval_status="draft",
    )
    db.add(visit)
    await db.flush()
    return visit, owner, mgrs


async def test_zone_step_gives_every_manager_a_task_and_any_one_decides(engine_db_session):
    db = engine_db_session
    visit, owner, (m1, m2) = await _zone_setup(db, managers=2)
    await execute_action(db, "vms_visit", visit.id, "submit", owner.id, "requester")
    approve = [t for t in await _open_tasks(db, visit.id) if t.type == "approve_vms_visit"]
    assert sorted(t.assigned_user_id for t in approve) == sorted([m1.id, m2.id])
    assert {t.assigned_role for t in approve} == {"zone_manager"}

    result = await execute_action(db, "vms_visit", visit.id, "approve", m2.id, "requester")
    assert result.new_status == "approved"
    assert await _open_tasks(db, visit.id) == []   # m1's task closed too


async def test_zone_step_refuses_someone_who_is_not_a_zone_manager(engine_db_session):
    db = engine_db_session
    visit, owner, _mgrs = await _zone_setup(db, managers=1)
    stranger = User(id=uuid.uuid4(), full_name="Stranger", role="requester", is_active=True)
    db.add(stranger)
    await db.flush()
    await execute_action(db, "vms_visit", visit.id, "submit", owner.id, "requester")
    with pytest.raises(Exception):
        await execute_action(db, "vms_visit", visit.id, "approve", stranger.id, "requester")
    await db.refresh(visit)
    assert visit.approval_status == "submitted"


async def test_zone_step_without_managers_auto_skips(engine_db_session):
    db = engine_db_session
    visit, owner, _ = await _zone_setup(db, managers=0)
    result = await execute_action(db, "vms_visit", visit.id, "submit", owner.id, "requester")
    assert result.new_status == "approved"
    assert await _open_tasks(db, visit.id) == []


async def test_task_title_uses_the_zone_name_from_vms_zones(engine_db_session):
    db = engine_db_session
    visit, owner, _ = await _zone_setup(db, managers=1, zone_name="Cold Room")
    await execute_action(db, "vms_visit", visit.id, "submit", owner.id, "requester")
    (task,) = await _open_tasks(db, visit.id)
    assert task.title.endswith("— Cold Room, 2026-10-02")


async def test_resync_reissues_zone_tasks_to_the_snapshotted_managers(engine_db_session):
    from app.crud.engine import _resync_document
    db = engine_db_session
    visit, owner, (m1, m2) = await _zone_setup(db, managers=2)
    await execute_action(db, "vms_visit", visit.id, "submit", owner.id, "requester")
    # One manager's task got lost (e.g. a hand edit): resync brings it back.
    lost = next(t for t in await _open_tasks(db, visit.id) if t.assigned_user_id == m2.id)
    lost.is_completed = True
    await db.flush()
    summary = await _resync_document(db, "vms_visit", visit.id)
    assert summary and any("zone manager" in a for a in summary["actions"])
    open_ids = sorted(t.assigned_user_id for t in await _open_tasks(db, visit.id))
    assert open_ids == sorted([m1.id, m2.id])
    # Already right → nothing to do.
    assert await _resync_document(db, "vms_visit", visit.id) is None
