"""Tests for the cross-system data-maintenance admin (VMS)."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select

from tests.conftest import make_user, make_token, authed_client


def test_registry_has_vms_entities():
    from app.admin.registry import REGISTRY

    assert set(REGISTRY.keys()) == {"visit", "visitor", "health_declaration"}
    for spec in REGISTRY.values():
        assert spec.schema.fields            # non-empty
        assert callable(spec.cascade_preview)
        assert callable(spec.cascade_delete)


@pytest.mark.asyncio
async def test_vms_admin_endpoints(test_engine):
    user = await make_user(test_engine, role="system_admin")
    token = make_token(user.id, "system_admin")
    async with authed_client(token) as client:
        r = await client.get("/api/v1/admin/entities")
        assert r.status_code == 200
        body = r.json()
        keys = {e["key"] for e in body}
        assert {"visit", "visitor", "health_declaration"} <= keys
        for entry in body:
            if entry["key"] in {"visit", "visitor", "health_declaration"}:
                assert entry["system"] == "vms"

        r2 = await client.get("/api/v1/admin/visit")
        assert r2.status_code == 200
        body2 = r2.json()
        assert "items" in body2 and "total" in body2


@pytest.mark.asyncio
async def test_vms_records_cannot_be_deleted(test_engine):
    """Visits, visitors and health declarations are compliance records (CFIA /
    PIPEDA retention). Data Maintenance may edit them but never delete them —
    single, bulk and preview all refuse with 409, and nothing is removed."""
    from app.models.visit import Visit, VisitPurpose, AccessArea, VisitStatus
    from app.models.visitor import Visitor, VisitorType

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    host = await make_user(test_engine, role="requester")
    admin = await make_user(test_engine, role="system_admin")
    visitor_id, visit_id = uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(Visitor(id=visitor_id, first_name="Jane", last_name="Doe",
                       company_name="Acme Corp", visitor_type=VisitorType.supplier))
        await db.commit()
    async with factory() as db:
        db.add(Visit(id=visit_id, visitor_id=visitor_id, additional_visitor_ids=[],
                     host_id=host.id, created_by=host.id, visit_date=date.today(),
                     planned_arrival=datetime.now(timezone.utc), visit_purpose=VisitPurpose.meeting,
                     access_area=AccessArea.office, status=VisitStatus.confirmed,
                     visit_title="Visit with Jane Doe (Acme Corp)"))
        await db.commit()

    async with authed_client(make_token(admin.id, "system_admin")) as client:
        entities = {e["key"]: e for e in (await client.get("/api/v1/admin/entities")).json()}
        assert all(entities[k]["allow_delete"] is False for k in ("visit", "visitor", "health_declaration"))

        assert (await client.delete(f"/api/v1/admin/visitor/{visitor_id}")).status_code == 409
        assert (await client.delete(f"/api/v1/admin/visit/{visit_id}?preview=1")).status_code == 409
        r = await client.post("/api/v1/admin/visit/bulk-delete", json={"ids": [str(visit_id)]})
        assert r.status_code == 409
        assert "Cancel the visit" in r.json()["detail"]

    async with factory() as db:
        assert (await db.execute(select(Visitor).where(Visitor.id == visitor_id))).scalar_one_or_none()
        assert (await db.execute(select(Visit).where(Visit.id == visit_id))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_visit_edit_reschedules_planned_times(test_engine):
    """Data Maintenance reschedules a visit through planned_arrival /
    planned_departure — the fields the Visit List, the detail page and the
    no-show job read. visit_date is read-only and follows planned_arrival's
    plant-local day; a time typed without an offset is plant time."""
    from app.models.admin_audit_log import AdminAuditLog
    from app.models.visit import Visit, VisitPurpose, AccessArea, VisitStatus
    from app.models.visitor import Visitor, VisitorType

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    host = await make_user(test_engine, role="requester")
    admin = await make_user(test_engine, role="system_admin")
    visitor_id, visit_id = uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(Visitor(id=visitor_id, first_name="Mike", last_name="Reschedule",
                       company_name="Acme Corp", visitor_type=VisitorType.contractor))
        await db.commit()
    async with factory() as db:
        db.add(Visit(id=visit_id, visitor_id=visitor_id, additional_visitor_ids=[],
                     host_id=host.id, created_by=host.id, visit_date=date(2026, 10, 20),
                     planned_arrival=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc),
                     planned_departure=datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc),
                     visit_purpose=VisitPurpose.maintenance,
                     access_area=AccessArea.warehouse, status=VisitStatus.confirmed,
                     visit_title="Visit with Mike Reschedule (Acme Corp)"))
        await db.commit()

    async with authed_client(make_token(admin.id, "system_admin")) as client:
        entities = {e["key"]: e for e in (await client.get("/api/v1/admin/entities")).json()}
        fields = {f["name"]: f for f in entities["visit"]["fields"]}
        assert fields["planned_arrival"]["editable"] is True
        assert fields["planned_departure"]["editable"] is True
        assert fields["visit_date"]["editable"] is False

        rec = (await client.get(f"/api/v1/admin/visit/{visit_id}")).json()
        assert rec["planned_arrival"] == "2026-10-20T08:00:00-04:00"   # plant time, not UTC

        # visit_date alone can no longer be edited.
        r = await client.patch(f"/api/v1/admin/visit/{visit_id}", json={"visit_date": "2026-10-21"})
        assert r.status_code == 400

        # Departure before arrival is refused, nothing written.
        r = await client.patch(f"/api/v1/admin/visit/{visit_id}", json={
            "planned_arrival": "2026-10-21T08:00", "planned_departure": "2026-10-21T07:00"})
        assert r.status_code == 400

        r = await client.patch(f"/api/v1/admin/visit/{visit_id}", json={
            "planned_arrival": "2026-10-21T08:00", "planned_departure": "2026-10-21T17:00-04:00"})
        assert r.status_code == 200, r.text
        assert r.json()["visit_date"] == "2026-10-21"

        # A late-evening plant time is still that plant day, not the UTC next day.
        r = await client.patch(f"/api/v1/admin/visit/{visit_id}", json={
            "planned_arrival": "2026-10-21T21:30", "planned_departure": None})
        assert r.status_code == 200, r.text
        assert r.json()["visit_date"] == "2026-10-21"

        r = await client.patch(f"/api/v1/admin/visit/{visit_id}", json={
            "planned_arrival": "2026-10-21T08:00", "planned_departure": "2026-10-21T17:00"})
        assert r.status_code == 200, r.text

    async with factory() as db:
        v = (await db.execute(select(Visit).where(Visit.id == visit_id))).scalar_one()
        assert v.planned_arrival == datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
        assert v.planned_departure == datetime(2026, 10, 21, 21, 0, tzinfo=timezone.utc)
        assert v.visit_date == date(2026, 10, 21)
        logs = (await db.execute(
            select(AdminAuditLog).where(AdminAuditLog.record_id == visit_id)
        )).scalars().all()
        # The re-derived visit_date is recorded even though it was not in the patch.
        assert any(l.before.get("visit_date") == "2026-10-20" and l.after.get("visit_date") == "2026-10-21"
                   for l in logs)


async def _no_showed_visit(test_engine, *, pending: bool):
    """A visit the scheduler has just marked No Show (planned 3h ago), with
    its approval task closed by the no-show and an older task a person
    completed. Returns (visit_id, approve_task_id, human_task_id, admin)."""
    from app.models.task_mirror import Task
    from app.models.visit import Visit, VisitPurpose, AccessArea, VisitStatus
    from app.models.visitor import Visitor, VisitorType
    from app.services.scheduled_jobs import mark_no_shows

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    host = await make_user(test_engine, role="requester")
    approver = await make_user(test_engine, role="dept_manager")
    admin = await make_user(test_engine, role="system_admin")
    now = datetime.now(timezone.utc)
    arrival = now - timedelta(hours=3)
    visitor_id, visit_id = uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(Visitor(id=visitor_id, first_name="Mike", last_name="NoShow",
                       company_name="Acme Corp", visitor_type=VisitorType.contractor))
        await db.commit()
    async with factory() as db:
        db.add(Visit(id=visit_id, visitor_id=visitor_id, additional_visitor_ids=[],
                     host_id=host.id, created_by=host.id, visit_date=arrival.date(),
                     planned_arrival=arrival, planned_departure=arrival + timedelta(hours=9),
                     visit_purpose=VisitPurpose.maintenance, access_area=AccessArea.warehouse,
                     status=VisitStatus.pending_approval if pending else VisitStatus.confirmed,
                     approval_status="submitted" if pending else "approved",
                     approval_step_idx=0, visit_title="Visit with Mike NoShow (Acme Corp)"))
        await db.flush()
        approve = Task(type="approve_vms_visit", document_type="vms_visit", document_id=visit_id,
                       document_number="x", assigned_role="dept_manager",
                       assigned_user_id=approver.id,
                       title=f"Approve visitor visit — Warehouse, {arrival.date().isoformat()}",
                       notified_at=now)
        human = Task(type="revise_vms_visit", document_type="vms_visit", document_id=visit_id,
                     document_number="x", assigned_role="requester", assigned_user_id=host.id,
                     title="Revise", is_completed=True, completed_by=host.id,
                     completed_at=now - timedelta(days=1))
        db.add_all([approve, human])
        await db.commit()
        approve_id, human_id = approve.id, human.id
    async with factory() as db:
        assert await mark_no_shows(db, now=now) == [visit_id]
        await db.commit()
    return visit_id, approve_id, human_id, admin


@pytest.mark.asyncio
async def test_dm_reschedule_undoes_no_show_and_reopens_approval(test_engine):
    """User rule 2026-10-20: rescheduling a No Show visit in Data Maintenance
    undoes the No Show. A visit that was pending goes back to Pending Approval
    with its approval task reopened (and re-queued for the mail outbox); the
    Host's approval-result mail is armed again."""
    from app.models.audit_log import AuditLog
    from app.models.task_mirror import Task
    from app.models.visit import Visit, VisitStatus

    visit_id, approve_id, human_id, admin = await _no_showed_visit(test_engine, pending=True)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        v = (await db.execute(select(Visit).where(Visit.id == visit_id))).scalar_one()
        assert v.status == VisitStatus.no_show and v.approval_status == "cancelled"
        assert v.host_notified_at is not None

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).astimezone().date().isoformat()
    async with authed_client(make_token(admin.id, "system_admin")) as client:
        r = await client.patch(f"/api/v1/admin/visit/{visit_id}", json={
            "status": "no_show",   # the edit form sends every editable field back
            "planned_arrival": f"{tomorrow}T08:00", "planned_departure": f"{tomorrow}T17:00"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "pending_approval"

    async with factory() as db:
        v = (await db.execute(select(Visit).where(Visit.id == visit_id))).scalar_one()
        assert v.status == VisitStatus.pending_approval
        assert v.approval_status == "submitted" and v.approval_step_idx == 0
        assert v.host_notified_at is None
        t = (await db.execute(select(Task).where(Task.id == approve_id))).scalar_one()
        assert t.is_completed is False and t.completed_at is None and t.notified_at is None
        assert v.visit_date.isoformat() in t.title
        h = (await db.execute(select(Task).where(Task.id == human_id))).scalar_one()
        assert h.is_completed is True          # a person's completion is not undone
        ev = (await db.execute(select(AuditLog).where(
            AuditLog.entity_id == visit_id, AuditLog.action_type == "visit.no_show_undone"))).scalars().all()
        assert len(ev) == 1 and ev[0].user_id == admin.id and ev[0].user_name == admin.full_name


@pytest.mark.asyncio
async def test_dm_undo_no_show_restores_confirmed_and_needs_future_schedule(test_engine):
    """A confirmed visit goes back to Confirmed. Saving it while the schedule is
    still overdue leaves it No Show — the next scheduler tick would mark it
    again anyway."""
    from app.models.visit import Visit, VisitStatus

    visit_id, _, _, admin = await _no_showed_visit(test_engine, pending=False)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with authed_client(make_token(admin.id, "system_admin")) as client:
        r = await client.patch(f"/api/v1/admin/visit/{visit_id}", json={"notes": "called, coming later"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "no_show"

        later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        r = await client.patch(f"/api/v1/admin/visit/{visit_id}", json={"planned_arrival": later})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "confirmed"

    async with factory() as db:
        v = (await db.execute(select(Visit).where(Visit.id == visit_id))).scalar_one()
        assert v.status == VisitStatus.confirmed and v.approval_status == "approved"


@pytest.mark.asyncio
async def test_dm_status_set_by_hand_is_not_overridden(test_engine):
    """An explicit status change in the same save wins over the undo."""
    visit_id, _, _, admin = await _no_showed_visit(test_engine, pending=True)
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    async with authed_client(make_token(admin.id, "system_admin")) as client:
        r = await client.patch(f"/api/v1/admin/visit/{visit_id}", json={
            "status": "cancelled", "planned_arrival": f"{tomorrow}T08:00",
            "planned_departure": f"{tomorrow}T17:00"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled"
