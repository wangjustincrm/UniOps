"""PR service owner — who confirms a service/project receipt.

`purchase_requests.owner_id` names the person who confirms the service was
delivered, for procurement types 4 (Service) and 6 (Project-Related). It is
nullable and NULL means "the requester", so the fallback
(`app/crud/pr_owner.py`) is the thing most of these tests are about: every PR
that existed before the column did means "the requester", and getting that
wrong would hand every legacy service PO's reminder to a NULL assignee — i.e.
a requester-role broadcast, the 2026-08-05 failure mode.

Covered here: the write paths (create default / create override / rejection of
an unusable owner / PATCH), the read shape (`owner_name` resolves the
fallback), reassignment of already-open receipt tasks, the visibility widening
that keeps a named owner from 404-ing on the PR they are chased about, and the
invoice-matched nudge (the second of the three paths that raise the same
confirm_receipt task). The completion-date sweep is covered in
test_service_gr_due.py; POST /gr admission in test_gr_create_authz.py.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.crud.pr import reassign_open_receipt_tasks
from app.crud.pr_owner import get_pr_owner_id, owner_id_of
from app.main import create_app
from app.models.department import Department
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio

PR_URL = "/api/v1/pr"
VENDOR_URL = "/api/v1/vendors"

_LINE = {"description": "Annual duct cleaning", "qty": "1", "unit": "EA",
         "unit_price": "1000.00"}


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def _client_for(user):
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {create_access_token(str(user.id), user.role)}"},
    )


async def _user(db, *, role: str = "requester", name: str = "Person",
                department_id=None, is_active: bool = True):
    u = await user_crud.create(db, RegisterRequest(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name=name, role=role))
    if department_id is not None:
        u.department_id = department_id
    if not is_active:
        u.is_active = False
    await db.flush()
    return u


async def _vendor(db) -> Vendor:
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme Services",
               category="supplier", contact_name="AP", contact_email="ap@acme.example")
    db.add(v)
    await db.flush()
    return v


async def _create_pr(client, **overrides) -> tuple[int, dict]:
    body = {
        "title": "Annual duct cleaning",
        "type": 4,
        "currency": "CAD",
        "service_completion_date": str(date.today() + timedelta(days=30)),
        "line_items": [_LINE],
    }
    body.update(overrides)
    resp = await client.post(PR_URL, json=body)
    return resp.status_code, (resp.json() if resp.content else {})


# ── The fallback itself ───────────────────────────────────────────────────────

async def test_owner_id_of_falls_back_to_the_requester(test_engine):
    """The whole feature rests on this: NULL owner_id is not "nobody"."""
    async with _factory(test_engine)() as db:
        requester = await _user(db, name="Ryan Requester")
        pr = PurchaseRequest(
            number=f"PR-OWN-{uuid.uuid4().hex[:6]}x", title="Svc", type=4,
            status="draft", currency="CAD", amount=Decimal("1000.00"),
            created_by=requester.id,
        )
        db.add(pr)
        await db.flush()

        assert owner_id_of(pr) == requester.id
        assert await get_pr_owner_id(db, pr.id) == requester.id

        owner = await _user(db, name="Olive Owner")
        pr.owner_id = owner.id
        await db.flush()
        assert owner_id_of(pr) == owner.id
        assert await get_pr_owner_id(db, pr.id) == owner.id


@pytest.mark.parametrize("physical_type", [1, 2, 3, 5])
async def test_a_stale_owner_on_a_physical_pr_is_inert(test_engine, physical_type):
    """The form only offers the field for types 4/6, but a draft edited from 4
    to 2 keeps its owner_id (PATCH leaves absent fields alone). Without the
    type check, that row would route a warehouse `collect_goods` task to a
    service owner."""
    async with _factory(test_engine)() as db:
        requester = await _user(db, name="Ryan Requester")
        stale_owner = await _user(db, name="Olive Owner")
        pr = PurchaseRequest(
            number=f"PR-STALE-{uuid.uuid4().hex[:6]}x", title="Was a service PR",
            type=physical_type, status="draft", currency="CAD",
            amount=Decimal("1000.00"), created_by=requester.id,
            owner_id=stale_owner.id,
        )
        db.add(pr)
        await db.flush()

        assert owner_id_of(pr) == requester.id
        assert await get_pr_owner_id(db, pr.id) == requester.id

        # Admission half: the SAME row as a service type does honour the owner,
        # so the assertions above are about the type and not about the column
        # being ignored everywhere.
        pr.type = 4
        await db.flush()
        assert owner_id_of(pr) == stale_owner.id
        assert await get_pr_owner_id(db, pr.id) == stale_owner.id


async def test_get_pr_owner_id_is_none_without_a_pr(test_engine):
    """The `no PR → skip the requester chain` branch in crud.gr reads this, so
    it must stay None-for-no-PR exactly like get_pr_requester_id."""
    async with _factory(test_engine)() as db:
        assert await get_pr_owner_id(db, None) is None
        assert await get_pr_owner_id(db, uuid.uuid4()) is None


# ── Create ────────────────────────────────────────────────────────────────────

async def test_create_without_an_owner_leaves_the_column_null(test_engine):
    """Deliberately NOT coalesced to created_by on write: a column repeating the
    requester cannot be told apart from one deliberately set to them."""
    async with _factory(test_engine)() as db:
        requester = await _user(db, name="Ryan Requester")
        await db.commit()

    async with _client_for(requester) as c:
        code, body = await _create_pr(c)
    assert code == 201, body
    assert body["owner_id"] is None
    # …but the response still names a person: owner_name resolves the fallback,
    # so the Detail page never renders a blank Owner row.
    assert body["owner_name"] == "Ryan Requester"


async def test_create_with_an_owner_stores_and_returns_it(test_engine):
    async with _factory(test_engine)() as db:
        requester = await _user(db, name="Ryan Requester")
        owner = await _user(db, name="Olive Owner")
        await db.commit()

    async with _client_for(requester) as c:
        code, body = await _create_pr(c, owner_id=str(owner.id))
    assert code == 201, body
    assert body["owner_id"] == str(owner.id)
    assert body["owner_name"] == "Olive Owner"
    assert body["created_by_name"] == "Ryan Requester", "requester label unchanged"


async def test_naming_the_requester_explicitly_is_stored_as_such(test_engine):
    """The Create form is a populated combo box: for types 4/6 it ALWAYS sends
    owner_id, including when it still shows the requester. That has to land as
    a real id, not be normalised back to NULL — otherwise "I looked at this
    field and left it on me" and "this field did not exist" become the same
    row, and the DM screen would show the Service Owner as empty."""
    async with _factory(test_engine)() as db:
        requester = await _user(db, name="Ryan Requester")
        await db.commit()

    async with _client_for(requester) as c:
        code, body = await _create_pr(c, owner_id=str(requester.id))
    assert code == 201, body
    assert body["owner_id"] == str(requester.id)
    assert body["owner_name"] == "Ryan Requester"


async def test_create_rejects_an_owner_who_does_not_exist(test_engine):
    async with _factory(test_engine)() as db:
        requester = await _user(db)
        await db.commit()

    async with _client_for(requester) as c:
        code, body = await _create_pr(c, owner_id=str(uuid.uuid4()))
    assert code == 422, body


async def test_create_rejects_a_deactivated_owner(test_engine):
    """A deactivated owner passes the FK and then silently swallows every
    reminder months later — refuse it while a human is still looking."""
    async with _factory(test_engine)() as db:
        requester = await _user(db)
        gone = await _user(db, name="Gone Away", is_active=False)
        await db.commit()

    async with _client_for(requester) as c:
        code, body = await _create_pr(c, owner_id=str(gone.id))
    assert code == 422, body

    # Admission half of the pair: the same call with an ACTIVE owner succeeds,
    # so the 422 above is about this owner and not about the payload.
    async with _factory(test_engine)() as db:
        ok_owner = await _user(db, name="Still Here")
        await db.commit()
    async with _client_for(requester) as c:
        code, body = await _create_pr(c, owner_id=str(ok_owner.id))
    assert code == 201, body


# ── Update ────────────────────────────────────────────────────────────────────

async def test_patch_moves_the_owner(test_engine):
    async with _factory(test_engine)() as db:
        requester = await _user(db, name="Ryan Requester")
        owner = await _user(db, name="Olive Owner")
        await db.commit()

    async with _client_for(requester) as c:
        code, created = await _create_pr(c)
        assert code == 201, created
        resp = await c.patch(f"{PR_URL}/{created['id']}",
                             json={"owner_id": str(owner.id)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner_id"] == str(owner.id)
    assert resp.json()["owner_name"] == "Olive Owner"


async def test_patch_without_owner_id_leaves_the_owner_alone(test_engine):
    """`undefined` means "don't touch" for every other PrUpdate field, and the
    Edit page omits owner_id on non-service types — a PATCH that clears the
    owner because it did not mention it would be the bug."""
    async with _factory(test_engine)() as db:
        requester = await _user(db)
        owner = await _user(db, name="Olive Owner")
        await db.commit()

    async with _client_for(requester) as c:
        code, created = await _create_pr(c, owner_id=str(owner.id))
        assert code == 201, created
        resp = await c.patch(f"{PR_URL}/{created['id']}", json={"title": "Renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner_id"] == str(owner.id)


# ── Reassignment of already-open receipt tasks ───────────────────────────────

async def _pr_po_with_task(db, *, task_type="confirm_receipt", doc="po",
                           pool=False, completed=False):
    requester = await _user(db, name="Ryan Requester")
    v = await _vendor(db)
    pr = PurchaseRequest(
        number=f"PR-RE-{uuid.uuid4().hex[:6]}x", title="Svc", type=4,
        status="approved", currency="CAD", amount=Decimal("1000.00"),
        vendor_id=v.id, vendor_name=v.name, created_by=requester.id,
    )
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(
        number=f"PO-RE-{uuid.uuid4().hex[:6]}x", title="Svc", type=4,
        status="issued", vendor_id=v.id, vendor_name=v.name, currency="CAD",
        subtotal=Decimal("1000.00"), tax_amount=Decimal("0.00"),
        total=Decimal("1000.00"), created_by=requester.id, pr_id=pr.id,
    )
    db.add(po)
    await db.flush()
    task = Task(
        type=task_type, priority="normal", document_type=doc, document_id=po.id,
        document_number=po.number,
        # pool=True reproduces the PHYSICAL shape: the warehouse pool holds it,
        # nobody personally.
        assigned_role="warehouse_staff" if pool else "requester",
        assigned_user_id=None if pool else requester.id,
        title="Confirm service completion", is_completed=completed,
    )
    db.add(task)
    await db.flush()
    return pr, po, task, requester


async def test_changing_the_owner_moves_the_open_confirm_receipt_task(test_engine):
    """The three creators resolve the owner once and never revisit it, so
    without this the new owner's inbox stays empty and the old one keeps a
    to-do they can no longer answer."""
    async with _factory(test_engine)() as db:
        pr, po, task, requester = await _pr_po_with_task(db)
        new_owner = await _user(db, name="Olive Owner")
        pr.owner_id = new_owner.id
        await db.flush()

        moved = await reassign_open_receipt_tasks(db, pr)
        await db.refresh(task)

    assert moved == 1
    assert task.assigned_user_id == new_owner.id
    assert task.assigned_user_id != requester.id


async def test_completed_receipt_tasks_are_never_reassigned(test_engine):
    """A completed task is history, not a to-do."""
    async with _factory(test_engine)() as db:
        pr, po, task, requester = await _pr_po_with_task(db, completed=True)
        new_owner = await _user(db, name="Olive Owner")
        pr.owner_id = new_owner.id
        await db.flush()

        moved = await reassign_open_receipt_tasks(db, pr)
        await db.refresh(task)

    assert moved == 0
    assert task.assigned_user_id == requester.id


async def test_a_role_pool_task_is_not_hijacked_to_a_person(test_engine):
    """A confirm_receipt on a PHYSICAL PO belongs to the warehouse pool
    (assigned_role=warehouse_staff, assignee NULL) — it was never routed off
    the PR, so pinning it to the PR's owner would take it away from the pool."""
    async with _factory(test_engine)() as db:
        pr, po, task, _ = await _pr_po_with_task(db, pool=True)
        owner = await _user(db, name="Olive Owner")
        pr.owner_id = owner.id
        await db.flush()

        moved = await reassign_open_receipt_tasks(db, pr)
        await db.refresh(task)

    assert moved == 0
    assert task.assigned_user_id is None
    assert task.assigned_role == "warehouse_staff"


async def test_unrelated_task_types_are_left_alone(test_engine):
    """create_pa is the requester's job — paying the vendor did not move."""
    async with _factory(test_engine)() as db:
        pr, po, task, requester = await _pr_po_with_task(db, task_type="create_pa")
        owner = await _user(db, name="Olive Owner")
        pr.owner_id = owner.id
        await db.flush()

        moved = await reassign_open_receipt_tasks(db, pr)
        await db.refresh(task)

    assert moved == 0
    assert task.assigned_user_id == requester.id


# ── Visibility ────────────────────────────────────────────────────────────────

async def test_a_named_owner_can_open_the_pr_they_will_be_chased_about(test_engine):
    """A service PR raised by one department for an owner in another would
    otherwise 404 for the very person asked to confirm it — and not only once
    the task exists: the owner has to be able to reach the PO and create the GR
    BEFORE the completion date passes."""
    async with _factory(test_engine)() as db:
        req_dept = Department(code=f"D{uuid.uuid4().hex[:6]}", name="Admin")
        own_dept = Department(code=f"D{uuid.uuid4().hex[:6]}", name="Engineering")
        db.add_all([req_dept, own_dept])
        await db.flush()
        requester = await _user(db, name="Ryan Requester", department_id=req_dept.id)
        owner = await _user(db, name="Olive Owner", department_id=own_dept.id)
        stranger = await _user(db, name="Sam Stranger", department_id=own_dept.id)
        await db.commit()

    async with _client_for(requester) as c:
        code, created = await _create_pr(c, owner_id=str(owner.id))
    assert code == 201, created

    async with _client_for(owner) as c:
        resp = await c.get(f"{PR_URL}/{created['id']}")
    assert resp.status_code == 200, "the named owner must be able to open it"

    # The denial half: a plain requester in the SAME department as the owner,
    # who simply is not the owner, still cannot. Without this the assertion
    # above would also pass if the scope had been widened to everybody.
    async with _client_for(stranger) as c:
        resp = await c.get(f"{PR_URL}/{created['id']}")
    assert resp.status_code == 404


# ── The invoice-matched path ─────────────────────────────────────────────────

async def test_the_invoice_matched_nudge_also_goes_to_the_owner(
    test_engine, monkeypatch,
):
    """Same confirm_receipt task, same PO, raised by a different trigger — the
    two paths reuse each other's row, so a different assignee rule here would
    mean whichever fired first decided who owns it."""
    import app.api.v1.invoices as invoices_mod
    from app.api.v1.invoices import _create_or_renotify_confirm_receipt

    # The real notifier spawns a background coroutine on its own DB session;
    # this test is about WHO the row names, so keep the dispatch out of it.
    monkeypatch.setattr(invoices_mod, "fire_and_forget_notify",
                        lambda *a, **k: None)

    async with _factory(test_engine)() as db:
        pr, po, existing, requester = await _pr_po_with_task(db)
        # Start from a clean PO: this path is about creating the task.
        await db.delete(existing)
        owner = await _user(db, name="Olive Owner")
        pr.owner_id = owner.id
        await db.flush()

        class _Inv:
            internal_ref = "INV-TEST-0001"

        await _create_or_renotify_confirm_receipt(
            db, po, pr, _Inv(), False)   # physical=False → service branch
        task = (await db.execute(select(Task).where(
            Task.type == "confirm_receipt",
            Task.document_type == "po",
            Task.document_id == po.id,
            Task.is_completed.is_(False),
        ))).scalars().one()

    assert task.assigned_user_id == owner.id
    assert task.assigned_user_id != requester.id
    assert task.assigned_role == "requester"
