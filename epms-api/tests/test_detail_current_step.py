"""GET of a single PR / PO / Agreement carries `current_step` (ISS-0085).

The Approval Timeline's current step showed only the step name and "Awaiting
action": the detail responses never carried who the open task is with, so the
page fell back to whoever had approved that step index earlier — after a
recall that was the previous round's approver, not the person it now waits on.
The list's Status cell already read the open task (enrich_current_step); the
detail endpoints now read the same thing.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import update

import app.db.session as sm
from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def _approver(db, name):
    return await user_crud.create(db, RegisterRequest(
        email=f"cs-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name=name, role="dept_manager"))


def _open_task(doc_type, doc_id, number, *, role, user_id):
    return Task(
        type=f"approve_{doc_type}", priority="normal", document_type=doc_type,
        document_id=doc_id, document_number=number, assigned_role=role,
        assigned_user_id=user_id, title="Approve", is_completed=False,
    )


async def test_po_detail_names_the_open_step_approver(admin_client):
    async with sm.AsyncSessionLocal() as db:
        approver = await _approver(db, "Pat Procurement")
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
                        contact_name="C", contact_email="c@x.com")
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=2,
                           vendor_id=vendor.id, vendor_name="Acme", status="submitted",
                           created_by=approver.id, total=Decimal("100"))
        db.add(po)
        await db.flush()
        db.add(_open_task("po", po.id, po.number, role="procurement_manager",
                          user_id=approver.id))
        await db.commit()
        po_id = po.id

    resp = await admin_client.get(f"/api/v1/po/{po_id}")
    assert resp.status_code == 200, resp.text
    cs = resp.json()["current_step"]
    assert cs is not None
    assert cs["label"] == "Procurement Manager"
    assert cs["approver_name"] == "Pat Procurement"


async def test_agreement_detail_names_the_open_step_approver(admin_client):
    async with sm.AsyncSessionLocal() as db:
        approver = await _approver(db, "Dana Manager")
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Bell", category="supplier",
                        contact_name="AP", contact_email="ap@bell.example")
        db.add(vendor)
        await db.flush()
        agr = PurchaseAgreement(
            number=f"AGR-CS-{uuid.uuid4().hex[:8]}", title="Bell", agreement_type="recurring",
            vendor_id=vendor.id, vendor_name=vendor.name,
            valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
            status="submitted", created_by=approver.id)
        db.add(agr)
        await db.flush()
        db.add(_open_task("agr", agr.id, agr.number, role="dept_manager",
                          user_id=approver.id))
        await db.commit()
        agr_id = agr.id

    resp = await admin_client.get(f"/api/v1/agreements/{agr_id}")
    assert resp.status_code == 200, resp.text
    cs = resp.json()["current_step"]
    assert cs is not None
    assert cs["label"] == "Dept Manager"
    assert cs["approver_name"] == "Dana Manager"


async def test_pr_detail_current_step_follows_the_open_task(admin_client):
    """Submitted → the open task's holder; draft (e.g. after a recall) → none."""
    resp = await admin_client.post("/api/v1/pr", json={
        "title": "Gloves", "type": 2, "currency": "CAD", "required_by": "2026-12-01",
        "line_items": [{"description": "Nitrile gloves", "qty": "1", "unit": "box",
                        "unit_price": "10.00", "line_total": "10.00"}],
    })
    assert resp.status_code == 201, resp.text
    pr_id = uuid.UUID(resp.json()["id"])

    # A draft has no current step.
    resp = await admin_client.get(f"/api/v1/pr/{pr_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["current_step"] is None

    async with sm.AsyncSessionLocal() as db:
        approver = await _approver(db, "Morgan Manager")
        await db.execute(update(PurchaseRequest).where(PurchaseRequest.id == pr_id)
                         .values(status="submitted"))
        db.add(_open_task("pr", pr_id, "PR-X", role="dept_manager", user_id=approver.id))
        await db.commit()

    resp = await admin_client.get(f"/api/v1/pr/{pr_id}")
    assert resp.status_code == 200, resp.text
    cs = resp.json()["current_step"]
    assert cs is not None
    assert cs["label"] == "Dept Manager"
    assert cs["approver_name"] == "Morgan Manager"
