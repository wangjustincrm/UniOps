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
