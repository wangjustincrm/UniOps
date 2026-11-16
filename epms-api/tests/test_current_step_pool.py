"""A role-pool step names the post's holders (ISS-0085 / ISS-0088).

Every PO step (Procurement Manager, GM / OPM) and most PA / agreement /
over-budget PR steps are routed to a role pool: the engine opens ONE task with
no assigned user. current_step.approver_name read only the task's assignee, so
on those steps the Approval Timeline showed the bare post ("Procurement
Manager · Awaiting action") while a PR's Department Manager step showed
"<step> — <name>". PRD §3.2.4 / line 475: the current step names who it is
waiting on; §3.3 role tokens: a post step goes to the post's current holders.

Seeded holders are named "AAAA…" so they sort first among any holders other
tests left in the shared database, and are deactivated afterwards so they do
not become holders for later tests; their open tasks are closed too.
"""
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as sm
from app.core.delegation import local_today
from app.core.security import hash_password
from app.crud.current_step import enrich_current_step
from app.models.po import PurchaseOrder
from app.models.task import Task
from app.models.user import User
from app.models.vendor import Vendor

pytestmark = pytest.mark.asyncio

TAG = uuid.uuid4().hex[:6]


def _user(name, role):
    uid = uuid.uuid4()
    return User(id=uid, email=f"pool-{uid.hex[:8]}-{TAG}@example.com",
                hashed_password=hash_password("x"), full_name=name,
                role=role, is_active=True)


def _pool_task(doc_type, doc_id, role):
    return Task(id=uuid.uuid4(), type=f"approve_{doc_type}", priority="normal",
                document_type=doc_type, document_id=doc_id,
                document_number=f"{doc_type.upper()}-{TAG}", assigned_role=role,
                assigned_user_id=None, title="Approve", is_completed=False)


async def _deactivate(db, users):
    """Leave nothing behind for later tests: the holders stop holding the
    post and this module's open pool tasks are closed (a dashboard test counts
    every open pool task of a role)."""
    await db.execute(update(User).where(User.id.in_([u.id for u in users]))
                     .values(is_active=False))
    await db.execute(update(Task).where(Task.document_number.like(f"%-{TAG}"))
                     .values(is_completed=True))
    await db.commit()


async def test_pool_step_names_the_post_holder_and_stand_in(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    doc_id = uuid.uuid4()
    async with sf() as db:
        holder = _user(f"AAAA Raghul {TAG}", "finance_director")
        stand_in = _user(f"Stand In {TAG}", "requester")
        db.add_all([holder, stand_in])
        await db.flush()
        today = local_today()
        await db.execute(text(
            "INSERT INTO approval_delegations (id, delegator_user_id, delegate_user_id, "
            " start_date, end_date, revoked_at, created_by) "
            "VALUES (:id, :a, :b, :s, :e, NULL, :c)"),
            {"id": uuid.uuid4(), "a": holder.id, "b": stand_in.id,
             "s": today - timedelta(days=1), "e": today + timedelta(days=1),
             "c": uuid.uuid4()})
        db.add(_pool_task("pa", doc_id, "finance_director"))
        await db.commit()

        items = [SimpleNamespace(id=doc_id, status="in_review")]
        await enrich_current_step(db, "pa", items)
        await _deactivate(db, [holder, stand_in])

    name = items[0].current_step["approver_name"]
    assert name is not None, "a pool step must name the post's holder"
    assert name.startswith(f"AAAA Raghul {TAG} (delegated: Stand In {TAG})")


async def test_wide_pool_shows_three_names_and_a_count(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    doc_id = uuid.uuid4()
    async with sf() as db:
        holders = [_user(f"AAAA{i} {TAG}", "vendor_manager") for i in range(1, 5)]
        db.add_all(holders)
        db.add(_pool_task("pa", doc_id, "vendor_manager"))
        await db.commit()

        items = [SimpleNamespace(id=doc_id, status="submitted")]
        await enrich_current_step(db, "pa", items)
        await _deactivate(db, holders)

    name = items[0].current_step["approver_name"]
    assert name.startswith(f"AAAA1 {TAG}, AAAA2 {TAG}, AAAA3 {TAG} +"), name


async def test_personal_role_pool_task_still_names_no_one(test_engine):
    """Only post / Finance BP / AP Clerk pools are resolved: a dept_manager
    task without an assignee is not the department's managers' pool."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    doc_id = uuid.uuid4()
    async with sf() as db:
        db.add(_pool_task("pr", doc_id, "dept_manager"))
        await db.commit()
        items = [SimpleNamespace(id=doc_id, status="in_review")]
        await enrich_current_step(db, "pr", items)
        await _deactivate(db, [])
    assert items[0].current_step["approver_name"] is None


async def test_po_detail_and_list_name_the_procurement_manager(admin_client):
    """The real PO shape: the Procurement Manager step is a role-pool task."""
    async with sm.AsyncSessionLocal() as db:
        holder = _user(f"AAAA Procurement {TAG}", "procurement_manager")
        db.add(holder)
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
                        contact_name="C", contact_email="c@x.com")
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(number=f"PO-POOL-{TAG}", title="T", type=2,
                           vendor_id=vendor.id, vendor_name="Acme", status="submitted",
                           created_by=holder.id, total=Decimal("100"))
        db.add(po)
        await db.flush()
        db.add(_pool_task("po", po.id, "procurement_manager"))
        await db.commit()
        po_id, number = po.id, po.number

    try:
        detail = await admin_client.get(f"/api/v1/po/{po_id}")
        assert detail.status_code == 200, detail.text
        cs = detail.json()["current_step"]
        assert cs["label"] == "Procurement Manager"
        assert cs["approver_name"].startswith(f"AAAA Procurement {TAG}"), cs

        listed = await admin_client.get("/api/v1/po", params={"search": number})
        assert listed.status_code == 200, listed.text
        row = next(i for i in listed.json()["items"] if i["id"] == str(po_id))
        assert row["current_step"]["label"] == "Procurement Manager"
        assert row["current_step"]["approver_name"].startswith(f"AAAA Procurement {TAG}")
    finally:
        async with sm.AsyncSessionLocal() as db:
            await _deactivate(db, [holder])
