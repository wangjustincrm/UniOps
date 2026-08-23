"""Raising a PA on someone else's behalf: who may, and how far.

Creating a PA is gated by the epms.pa.write matrix key plus — for callers whose
JWT base role is 'requester' — an ownership check against the linked PR. A
Procurement Officer must be able to pay ANY PO, whether the role is their base
login role or an additional role layered on a requester account, while a plain
requester still may only pay their own requisitions.

Two shapes of on-behalf authority live here:
  • procurement_officer — ANY PO, company-wide (identity migration 0005).
  • dept_admin          — only POs whose requisition belongs to their OWN
                          department, matching the rows their access scope
                          already shows them (access_scope.visible_pr_subquery's
                          dept_manager/dept_admin branch).

Approval routing is NOT affected by who creates the PA (approval-api resolves
approvers from the linked PR) — that invariant is covered by
approval-api/tests/test_pa_on_behalf_routing.py.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

PA_URL = "/api/v1/pa"


def _pa_payload(po_id):
    return {
        "po_id": po_id, "title": "On-behalf Payment", "pa_type": "regular",
        "subtotal": "100.00", "tax_amount": "0.00", "currency": "CAD",
        "line_items": [{"description": "X", "qty": "1", "unit": "EA", "unit_price": "100.00"}],
    }


async def _grant_pa_write(db):
    """Mirror the real production grants in the shadow authz tables — conftest's
    default matrix seeds only the view_*/create_* keys, never the phase-2 ones.
    Grants epms.pa.write to both procurement_officer (identity migration 0005)
    and requester (identity-api/scripts/seed_phase2_keys.py:38), so a plain
    requester in these tests reaches create_pa's body exactly as they do in
    production — otherwise a "stranger" requester would be rejected by the
    require_permission dependency before the ownership check ever runs, and
    the regression test below would pass for the wrong reason."""
    await db.execute(text(
        "INSERT INTO permission_defs(key,module,label,sort) "
        "VALUES ('epms.pa.write','epms','Create / Edit PAs',102) ON CONFLICT (key) DO NOTHING"))
    await db.execute(text(
        "INSERT INTO role_permissions(role_code,permission_key) "
        "VALUES ('procurement_officer','epms.pa.write') ON CONFLICT DO NOTHING"))
    await db.execute(text(
        "INSERT INTO role_permissions(role_code,permission_key) "
        "VALUES ('requester','epms.pa.write') ON CONFLICT DO NOTHING"))
    # dept_admin holds it too — ticked in the Access Control Matrix by an admin
    # (there is no migration for it; the matrix is the record). Without this the
    # dept_admin tests below would 403 at require_permission and never reach the
    # ownership branch they exist to exercise.
    await db.execute(text(
        "INSERT INTO role_permissions(role_code,permission_key) "
        "VALUES ('dept_admin','epms.pa.write') ON CONFLICT DO NOTHING"))


async def _user(db, role: str, *, additional: str | None = None, department_id=None):
    u = await user_crud.create(db, RegisterRequest(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name=role.replace("_", " ").title(), role=role,
        department_id=department_id))
    if additional:
        await db.execute(text(
            "INSERT INTO user_roles(user_id, role_code) VALUES (:u, :r) "
            "ON CONFLICT DO NOTHING"), {"u": str(u.id), "r": additional})
    return u


# Departments/cost centres created below must not outlive this module: the
# conftest session builds the schema ONCE and every test file shares the rows
# left behind, and tests/test_access_scope_dept.py's fixture starts with
# `DELETE FROM departments` — which a leftover cost_centers row turns into a
# ForeignKeyViolationError (RESTRICT), erroring out that whole file depending on
# collection order. Tracked by id and undone in _clean_departments below.
_CREATED_DEPTS: list[uuid.UUID] = []
_CREATED_CCS: list[uuid.UUID] = []


async def _department(db, name: str):
    """A department plus one cost centre charged to it."""
    d = Department(code=f"D-{uuid.uuid4().hex[:8]}", name=name)
    db.add(d); await db.flush()
    cc = CostCenter(code=f"CC-{uuid.uuid4().hex[:8]}", name=f"{name} CC", department_id=d.id)
    db.add(cc); await db.flush()
    _CREATED_DEPTS.append(d.id); _CREATED_CCS.append(cc.id)
    return d, cc


@pytest.fixture(autouse=True)
async def _clean_departments(test_engine):
    """Drop this module's departments/cost centres after each test.

    The PR/PO/invoice rows themselves are left alone (harmless, and every other
    file leaves those too) — only their cost-centre reference is released so the
    RESTRICT FK lets the cost centre go."""
    yield
    if not _CREATED_DEPTS and not _CREATED_CCS:
        return
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        if _CREATED_CCS:
            await db.execute(text("UPDATE purchase_requests SET cost_center_id = NULL "
                                  "WHERE cost_center_id = ANY(:ids)"),
                             {"ids": [str(i) for i in _CREATED_CCS]})
            await db.execute(text("DELETE FROM cost_centers WHERE id = ANY(:ids)"),
                             {"ids": [str(i) for i in _CREATED_CCS]})
        if _CREATED_DEPTS:
            await db.execute(text("DELETE FROM departments WHERE id = ANY(:ids)"),
                             {"ids": [str(i) for i in _CREATED_DEPTS]})
        await db.commit()
    _CREATED_CCS.clear(); _CREATED_DEPTS.clear()


async def _three_way_po_owned_by(db, requester_id, *, cost_center_id=None):
    """A PO linked to a PR raised by `requester_id`, carrying a GR + matched
    invoice so the 3-way receipt gate is satisfied. Returns the PO id."""
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v); await db.flush()
    pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:8]}", title="Someone else's PR",
                         type=1, status="approved", amount=Decimal("100"),
                         created_by=requester_id, cost_center_id=cost_center_id)
    db.add(pr); await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="PO", type=1,
                       vendor_id=v.id, vendor_name="Acme", status="issued",
                       created_by=requester_id, pr_id=pr.id)
    db.add(po); await db.flush()
    gr = GoodsReceipt(number=f"GR-{uuid.uuid4().hex[:8]}", title="G", po_id=po.id,
                      po_number=po.number, vendor_id=v.id, vendor_name="Acme",
                      gr_type="physical", procurement_type=1, status="collected",
                      created_by=requester_id)
    db.add(gr); await db.flush()
    inv = Invoice(internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
                  vendor_id=v.id, vendor_name="Acme", amount=Decimal("100"),
                  tax_amount=Decimal("0"), total_amount=Decimal("100"),
                  invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
                  status="matched", line_items=[], po_id=po.id, gr_id=gr.id,
                  uploaded_by=requester_id)
    db.add(inv); await db.flush()
    return po.id


def _client_for(user):
    token = create_access_token(str(user.id), user.role)
    return AsyncClient(transport=ASGITransport(app=create_app()),
                       base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_procurement_officer_creates_pa_for_another_requesters_po(test_engine):
    """Base-role Procurement Officer: the matrix grant alone must be enough —
    the ownership branch does not apply to a non-requester base role."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_pa_write(db)
        requester = await _user(db, "requester")
        po_id = await _three_way_po_owned_by(db, requester.id)
        officer = await _user(db, "procurement_officer")
        await db.commit()
    async with _client_for(officer) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 201, r.text
    assert r.json()["pa_number"].startswith("PA-")


@pytest.mark.asyncio
async def test_requester_with_additional_procurement_officer_role_creates_pa(test_engine):
    """Granting Procurement Officer as an ADDITIONAL role on a requester login
    must work identically — the matrix unions roles, so the ownership 403 has to
    honour the additional role too."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_pa_write(db)
        owner = await _user(db, "requester")
        po_id = await _three_way_po_owned_by(db, owner.id)
        officer = await _user(db, "requester", additional="procurement_officer")
        await db.commit()
    async with _client_for(officer) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 201, r.text
    assert r.json()["pa_number"].startswith("PA-")


@pytest.mark.asyncio
async def test_plain_requester_still_cannot_create_pa_for_someone_elses_po(test_engine):
    """Regression guard: relaxing the ownership check for officers must not open
    it for ordinary requesters.

    The stranger must hold epms.pa.write (granted above, matching production —
    see identity-api/scripts/seed_phase2_keys.py:38) so the request reaches
    create_pa's body and is rejected by the ownership check specifically, not
    by the require_permission dependency before it ever runs. Asserting the
    detail message (not just the status code) is what makes this test load-
    bearing against a widened _may_create_pa_on_behalf."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_pa_write(db)
        owner = await _user(db, "requester")
        po_id = await _three_way_po_owned_by(db, owner.id)
        stranger = await _user(db, "requester")
        await db.commit()
    async with _client_for(stranger) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 403, r.text
    assert "You can only create payments for purchase orders linked to your own requisitions." in r.text


async def _open_create_pa_task_for(db, po, requester_id):
    """The create_pa task the live flow raises when an invoice is matched —
    always anchored on the PO and assigned to the PR requester."""
    t = Task(type="create_pa", document_type="po", document_id=po.id,
             document_number=po.number, assigned_role="requester",
             assigned_user_id=requester_id,
             title=f"Create Payment Application: {po.number}")
    db.add(t); await db.flush()
    return t.id


@pytest.mark.asyncio
async def test_officer_created_pa_leaves_no_task_for_the_officer(test_engine):
    """The create-PA reminder belongs to the PR requester and must stay there:
    creating the PA on their behalf completes THEIR task and must not raise any
    task (hence any e-mail or daily follow-up) aimed at the officer."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_pa_write(db)
        requester = await _user(db, "requester")
        po_id = await _three_way_po_owned_by(db, requester.id)
        po = await db.get(PurchaseOrder, po_id)
        task_id = await _open_create_pa_task_for(db, po, requester.id)
        officer = await _user(db, "procurement_officer")
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 201, r.text

    async with factory() as db:
        # The requester's reminder is done — they must not keep being chased.
        done = (await db.execute(
            select(Task.is_completed).where(Task.id == task_id))).scalar_one()
        assert done is True, "creating the PA must complete the requester's create_pa task"

        # Nothing at all points at the officer: no per-user assignment and no
        # create_pa pool broadcast to their role.
        mine = (await db.execute(
            select(Task).where(Task.assigned_user_id == officer.id))).scalars().all()
        assert mine == [], f"officer must receive no task, got {[t.type for t in mine]}"
        pooled = (await db.execute(
            select(Task).where(Task.type == "create_pa",
                               Task.assigned_role == "procurement_officer"))).scalars().all()
        assert pooled == [], "create_pa must never be broadcast to the procurement_officer pool"


# ── dept_admin: on-behalf, but only inside their own department ────────────────
#
# A Department Administrator raises payments for the people they administer.
# Granting them epms.pa.write in the matrix is NOT enough on its own: their JWT
# base role is `requester` (dept_admin is an ADDITIONAL role), so create_pa's
# ownership branch runs and rejects every PO that is not linked to a PR they
# raised themselves. These tests pin the department-scoped exemption — and,
# just as importantly, that it stops at the department boundary.


@pytest.mark.asyncio
async def test_dept_admin_creates_pa_for_own_department_requester(test_engine):
    """The reported case: requester + dept_admin paying for a colleague's PO.

    The requisitioner sits in the same department as the administrator, which is
    exactly the relationship `visible_pr_subquery` already uses to show her that
    PR — so being able to see it and being able to pay it must agree."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_pa_write(db)
        dept, _cc = await _department(db, "Maintenance")
        colleague = await _user(db, "requester", department_id=dept.id)
        po_id = await _three_way_po_owned_by(db, colleague.id)
        admin = await _user(db, "requester", additional="dept_admin", department_id=dept.id)
        await db.commit()
    async with _client_for(admin) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 201, r.text
    assert r.json()["pa_number"].startswith("PA-")


@pytest.mark.asyncio
async def test_dept_admin_creates_pa_for_pr_charged_to_own_department(test_engine):
    """Second half of the same visibility rule: a PR raised by an outsider but
    charged to one of HER department's cost centres is her department's spend
    (budget oversight), and `visible_pr_subquery` shows it to her for that
    reason. The payment gate must follow the same two conditions, not just the
    requisitioner-membership one."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_pa_write(db)
        dept, cc = await _department(db, "Warehouse")
        other, _ = await _department(db, "Elsewhere")
        outsider = await _user(db, "requester", department_id=other.id)
        po_id = await _three_way_po_owned_by(db, outsider.id, cost_center_id=cc.id)
        admin = await _user(db, "requester", additional="dept_admin", department_id=dept.id)
        await db.commit()
    async with _client_for(admin) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_dept_admin_cannot_create_pa_for_another_department(test_engine):
    """★ The boundary. dept_admin is a RESTRICTED role — widening the ownership
    check for it must not hand a department administrator a company-wide payment
    entry point (that is procurement_officer's job, and it is a deliberate,
    separately-granted one)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_pa_write(db)
        mine, _ = await _department(db, "Mine")
        theirs, their_cc = await _department(db, "Theirs")
        stranger = await _user(db, "requester", department_id=theirs.id)
        po_id = await _three_way_po_owned_by(db, stranger.id, cost_center_id=their_cc.id)
        admin = await _user(db, "requester", additional="dept_admin", department_id=mine.id)
        await db.commit()
    async with _client_for(admin) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 403, r.text
    assert "You can only create payments for purchase orders linked to your own requisitions." in r.text


@pytest.mark.asyncio
async def test_dept_admin_with_no_department_gets_no_exemption(test_engine):
    """A dept_admin whose user record carries no department administers nothing.

    Load-bearing against the obvious implementation slip: comparing a NULL
    department to the requisitioner's NULL department with `==` would make an
    unassigned administrator able to pay for every other unassigned user."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_pa_write(db)
        owner = await _user(db, "requester")               # no department either
        po_id = await _three_way_po_owned_by(db, owner.id)
        admin = await _user(db, "requester", additional="dept_admin")
        await db.commit()
    async with _client_for(admin) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 403, r.text
