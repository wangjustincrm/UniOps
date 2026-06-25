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
async def test_visitor_cascade_deletes_visits_first(test_engine):
    from app.models.visit import Visit, VisitPurpose, AccessArea, VisitStatus
    from app.models.visitor import Visitor, VisitorType
    from app.models.task_mirror import Task
    from app.models.admin_audit_log import AdminAuditLog
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Prerequisite: a User row to satisfy host_id / created_by FKs (RESTRICT).
    host = await make_user(test_engine, role="requester")

    visitor_id = uuid.uuid4()
    visit_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    async with factory() as db:
        db.add(Visitor(
            id=visitor_id,
            first_name="Jane",
            last_name="Doe",
            company_name="Acme Corp",
            visitor_type=VisitorType.supplier,
        ))
        await db.commit()

    async with factory() as db:
        db.add(Visit(
            id=visit_id,
            visitor_id=visitor_id,
            additional_visitor_ids=[],
            host_id=host.id,
            created_by=host.id,
            visit_date=date.today(),
            planned_arrival=datetime.now(timezone.utc),
            visit_purpose=VisitPurpose.meeting,
            access_area=AccessArea.office,
            status=VisitStatus.confirmed,
            visit_title="Visit with Jane Doe (Acme Corp)",
        ))
        db.add(Task(
            document_type="visit",
            document_id=visit_id,
            document_number="VMS-VISIT",
            type="visit_review",
            assigned_role="system_admin",
            title="Review visit",
        ))
        await db.commit()

    async with factory() as db:
        summary = await service.delete_record(
            db, "visitor", visitor_id, actor_id=actor_id, actor_email="",
        )
        await db.commit()

    assert summary["vms_visitors"] == 1
    assert summary["vms_visits"] >= 1

    async with factory() as db:
        visitor_row = (await db.execute(
            select(Visitor).where(Visitor.id == visitor_id)
        )).scalar_one_or_none()
        visit_row = (await db.execute(
            select(Visit).where(Visit.id == visit_id)
        )).scalar_one_or_none()
        assert visitor_row is None
        assert visit_row is None

        audit = (await db.execute(
            select(AdminAuditLog).where(
                AdminAuditLog.action == "delete",
                AdminAuditLog.system == "vms",
                AdminAuditLog.entity == "visitor",
                AdminAuditLog.record_id == visitor_id,
            )
        )).scalar_one_or_none()
        assert audit is not None
        assert audit.cascade_summary["vms_visitors"] == 1
        assert audit.cascade_summary["vms_visits"] >= 1
