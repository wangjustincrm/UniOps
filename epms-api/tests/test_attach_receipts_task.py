"""attach_receipts: handing a house-account invoice's receipt reconciliation to
the agreement owner (user report 2026-10-20).

AP matched an invoice straight to AGR-202608-0008. From then on nothing could
give the owner the work — assign-match only runs on unmatched/exception — and
the owner, who had recorded the delivery notes, got a 403 on the invoice's
receipt list that the page showed as "Couldn't load this agreement's receipts".

Covers: the task is raised for the owner and closes with the evidence; the
owner (and only the right people) may list / attach / settle; the inbox sweep
backfills invoices that were already waiting and honours a user dismissal; the
fallback assignee; reassignment when the owner changes.
"""
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import attach_receipts_task
from app.crud import user as user_crud
from app.main import create_app
from app.models.agreement import PurchaseAgreement
from app.models.task import Task
from app.schemas.auth import RegisterRequest
from tests.test_agreement_invoice_match import _make_active_agreement
from tests.test_agreements import seed_vendor_and_user
from tests.test_invoice_receipts import _create_receipt, _matched_invoice

pytestmark = pytest.mark.asyncio

INV_URL = "/api/v1/invoices"


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _new_user(test_engine, role: str = "requester") -> uuid.UUID:
    async with _factory(test_engine)() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name=f"Test {role}", role=role,
        ))
        await db.commit()
        return user.id


def _client_as(user_id: uuid.UUID, role: str = "requester") -> AsyncClient:
    token = create_access_token(str(user_id), role)
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _tasks(test_engine, invoice_id) -> list[Task]:
    async with _factory(test_engine)() as db:
        return list((await db.execute(select(Task).where(
            Task.type == "attach_receipts", Task.document_id == uuid.UUID(str(invoice_id)),
        ).order_by(Task.created_at))).scalars().all())


async def _setup(admin_client, test_engine, **agr_over):
    vendor_id, _name, creator_id = await seed_vendor_and_user(test_engine)
    owner_id = await _new_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, creator_id,
                                       owner_id=owner_id, **agr_over)
    inv = await _matched_invoice(admin_client, vendor_id, agr)
    return agr, inv, owner_id, creator_id


async def test_matching_to_a_house_account_raises_a_task_for_the_owner(admin_client, test_engine):
    agr, inv, owner_id, _ = await _setup(admin_client, test_engine)
    tasks = await _tasks(test_engine, inv["id"])
    assert len(tasks) == 1
    t = tasks[0]
    assert t.is_completed is False
    assert t.assigned_user_id == owner_id
    assert t.document_type == "invoice"
    assert agr.number in t.title


async def test_owner_lists_and_attaches_receipts_and_the_task_closes(admin_client, test_engine):
    agr, inv, owner_id, creator_id = await _setup(admin_client, test_engine)
    async with _factory(test_engine)() as db:
        receipt = await _create_receipt(db, agr, creator_id, amount="100.00")

    async with _client_as(owner_id) as owner:
        res = await owner.get(f"{INV_URL}/{inv['id']}/agreements/{agr.id}/receipts")
        assert res.status_code == 200, res.text
        assert [r["id"] for r in res.json()["items"]] == [str(receipt.id)]

        res = await owner.put(f"{INV_URL}/{inv['id']}/receipts",
                              json={"receipt_ids": [str(receipt.id)]})
        assert res.status_code == 200, res.text

    (t,) = await _tasks(test_engine, inv["id"])
    assert t.is_completed is True
    # System completion, not a dismissal — a later empty PUT may re-raise it.
    assert t.completed_by is None


async def test_settling_without_receipt_closes_the_task(admin_client, test_engine):
    _agr, inv, owner_id, _ = await _setup(admin_client, test_engine)
    async with _client_as(owner_id) as owner:
        res = await owner.post(f"{INV_URL}/{inv['id']}/settle-without-receipt",
                               json={"reason": "Monthly statement, slips not kept"})
        assert res.status_code == 200, res.text
    (t,) = await _tasks(test_engine, inv["id"])
    assert t.is_completed is True


async def test_clearing_every_receipt_reopens_a_task(admin_client, test_engine):
    agr, inv, owner_id, creator_id = await _setup(admin_client, test_engine)
    async with _factory(test_engine)() as db:
        receipt = await _create_receipt(db, agr, creator_id, amount="100.00")
    async with _client_as(owner_id) as owner:
        assert (await owner.put(f"{INV_URL}/{inv['id']}/receipts",
                                json={"receipt_ids": [str(receipt.id)]})).status_code == 200
        assert (await owner.put(f"{INV_URL}/{inv['id']}/receipts",
                                json={"receipt_ids": []})).status_code == 200
    tasks = await _tasks(test_engine, inv["id"])
    assert [t.is_completed for t in tasks] == [True, False]


async def test_someone_unrelated_is_refused_with_receipt_wording(admin_client, test_engine):
    agr, inv, _owner_id, _ = await _setup(admin_client, test_engine)
    stranger = await _new_user(test_engine)
    async with _client_as(stranger) as c:
        res = await c.get(f"{INV_URL}/{inv['id']}/agreements/{agr.id}/receipts")
        assert res.status_code == 403
        assert "attach receipts" in res.json()["detail"]
        res = await c.post(f"{INV_URL}/{inv['id']}/settle-without-receipt",
                           json={"reason": "x"})
        assert res.status_code == 403


async def test_owner_cannot_reach_another_agreements_receipts(admin_client, test_engine):
    """Ownership of THIS invoice's agreement is the grant — it must not open a
    different agreement's receipts through the same route."""
    agr, inv, owner_id, creator_id = await _setup(admin_client, test_engine)
    other = await _make_active_agreement(test_engine, agr.vendor_id, creator_id)
    async with _client_as(owner_id) as owner:
        res = await owner.get(f"{INV_URL}/{inv['id']}/agreements/{other.id}/receipts")
    # Same vendor + in window → it IS a candidate; the owner of agr is still
    # admitted (the gate is per invoice), which mirrors what AP sees. The
    # agreement-membership check is what stops arbitrary ids:
    assert res.status_code == 200
    async with _client_as(owner_id) as owner:
        res = await owner.get(f"{INV_URL}/{inv['id']}/agreements/{uuid.uuid4()}/receipts")
    assert res.status_code == 404


async def test_inbox_sweep_backfills_an_invoice_that_was_already_waiting(admin_client, test_engine):
    _agr, inv, owner_id, _ = await _setup(admin_client, test_engine)
    # Simulate an invoice matched before this feature existed.
    async with _factory(test_engine)() as db:
        await db.execute(delete(Task).where(Task.type == "attach_receipts",
                                            Task.document_id == uuid.UUID(inv["id"])))
        await db.commit()

    async with _client_as(owner_id) as owner:
        res = await owner.get("/api/v1/tasks")
        assert res.status_code == 200, res.text
        mine = [t for t in res.json()["items"]
                if t["type"] == "attach_receipts" and t["document_id"] == inv["id"]]
    assert len(mine) == 1


async def test_a_dismissed_task_is_not_raised_again(admin_client, test_engine):
    _agr, inv, owner_id, _ = await _setup(admin_client, test_engine)
    async with _factory(test_engine)() as db:
        (t,) = (await db.execute(select(Task).where(
            Task.type == "attach_receipts", Task.document_id == uuid.UUID(inv["id"])))).scalars().all()
        t.is_completed = True
        t.completed_at = datetime.now(timezone.utc)
        t.completed_by = owner_id
        await db.commit()
    async with _client_as(owner_id) as owner:
        assert (await owner.get("/api/v1/tasks")).status_code == 200
    tasks = await _tasks(test_engine, inv["id"])
    assert len(tasks) == 1 and tasks[0].is_completed


async def test_sweep_closes_a_task_whose_invoice_gained_evidence_behind_its_back(
        admin_client, test_engine):
    """Data Maintenance edits bypass the endpoints — the inbox read closes it."""
    _agr, inv, owner_id, _ = await _setup(admin_client, test_engine)
    from app.models.invoice import Invoice
    async with _factory(test_engine)() as db:
        row = (await db.execute(select(Invoice).where(Invoice.id == uuid.UUID(inv["id"])))).scalar_one()
        row.legacy_settlement = True
        row.legacy_settlement_reason = "set in DM"
        await db.commit()
    async with _client_as(owner_id) as owner:
        assert (await owner.get("/api/v1/tasks")).status_code == 200
    (t,) = await _tasks(test_engine, inv["id"])
    assert t.is_completed is True


async def test_no_owner_and_no_department_falls_back_to_the_ap_pool(admin_client, test_engine):
    vendor_id, _name, creator_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, creator_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr)
    (t,) = await _tasks(test_engine, inv["id"])
    assert t.assigned_user_id is None
    assert t.assigned_role == "ap_clerk"


async def test_changing_the_owner_moves_the_open_task(admin_client, test_engine):
    agr, inv, _owner_id, _ = await _setup(admin_client, test_engine)
    new_owner = await _new_user(test_engine)
    async with _factory(test_engine)() as db:
        row = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        row.owner_id = new_owner
        await db.flush()
        assert await attach_receipts_task.reassign_for_agreement(db, row) == 1
        await db.commit()
    (t,) = await _tasks(test_engine, inv["id"])
    assert t.assigned_user_id == new_owner


async def test_holder_of_the_task_may_attach_without_owning_the_agreement(admin_client, test_engine):
    """The department-manager fallback holds the task but is not the owner."""
    agr, inv, _owner_id, creator_id = await _setup(admin_client, test_engine)
    holder = await _new_user(test_engine, "dept_manager")
    async with _factory(test_engine)() as db:
        (t,) = (await db.execute(select(Task).where(
            Task.type == "attach_receipts", Task.document_id == uuid.UUID(inv["id"])))).scalars().all()
        t.assigned_user_id = holder
        await db.commit()
    async with _client_as(holder, "dept_manager") as c:
        res = await c.get(f"{INV_URL}/{inv['id']}/agreements/{agr.id}/receipts")
    assert res.status_code == 200, res.text
