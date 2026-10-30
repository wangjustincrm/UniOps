"""ISS-0168 / BUD-OI-11: a Department Manager's dashboard shows their own
department's Open POs / Total Committed; GM stays company-wide. The Budget
Overview and the over-budget count come from budget-api's scoped summary."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import dashboard as dash_crud
from app.crud import user as user_crud
from app.models.department import Department
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def _seed(db):
    dept = Department(code=f"D{uuid.uuid4().hex[:6]}", name="E-COM test")
    db.add(dept)
    await db.flush()
    mgr = await user_crud.create(db, RegisterRequest(
        email=f"dm-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Dee Manager", role="dept_manager"))
    mgr.department_id = dept.id
    vendor = Vendor(code=f"V-DS-{uuid.uuid4().hex[:6]}", name="Scope Vendor", category="x",
                    contact_name="c", contact_email="c@x.com", payment_terms="net30", currency="CAD")
    db.add(vendor)
    await db.flush()
    pr = PurchaseRequest(number=f"PR-DS-{uuid.uuid4().hex[:8]}", title="dept PR", type=2,
                         status="approved", amount=Decimal("11.30"), currency="CAD",
                         created_by=mgr.id, department_id=dept.id)
    db.add(pr)
    await db.flush()
    mine = PurchaseOrder(id=uuid.uuid4(), number=f"PO-DS-{uuid.uuid4().hex[:6]}", title="mine",
                         type=2, status="issued", vendor_id=vendor.id, vendor_name=vendor.name,
                         created_by=mgr.id, pr_id=pr.id, total=Decimal("11.30"))
    other = PurchaseOrder(id=uuid.uuid4(), number=f"PO-DS-{uuid.uuid4().hex[:6]}", title="other",
                          type=2, status="issued", vendor_id=vendor.id, vendor_name=vendor.name,
                          created_by=mgr.id, total=Decimal("500.00"))
    db.add_all([mine, other])
    await db.commit()
    return mgr


def _kpi(board, title):
    return next(k for k in board.kpis if k.title == title)


async def test_dept_manager_open_pos_are_their_departments(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        mgr = await _seed(db)
        user = {"sub": str(mgr.id), "role": "dept_manager"}
        board = await dash_crud.build(db, "dept_manager", mgr.id, user=user)
        assert _kpi(board, "Open POs").value == "1"
        assert _kpi(board, "Total Committed (FY)").value == "CAD 11.30"
        assert _kpi(board, "Open POs").subtitle == "Your department"

        # GM: company-wide, as before.
        everyone = (await db.execute(select(func.count()).select_from(PurchaseOrder).where(
            PurchaseOrder.status.notin_(["cancelled", "closed", "draft"])))).scalar_one()
        gm = await dash_crud.build(db, "gm", mgr.id, user={"sub": str(mgr.id), "role": "gm"})
        assert _kpi(gm, "Open POs").value == str(everyone)
        assert _kpi(gm, "Open POs").subtitle == "Company-wide"


async def test_budget_is_judged_per_cost_center_and_account(test_engine, monkeypatch):
    """BUD-OI-11 / ISS-0168: budget-api's own scope picks the cost centers; each
    is read on its own so one cost center's overrun cannot hide behind another's
    unspent budget on the same account. Over-Budget Depts counts departments
    with any over account; the subtitle gives the number of accounts."""
    from app.services import budget_client

    monkeypatch.setattr(dash_crud, "_cc_summary_cache", {})
    dept_a, dept_b = str(uuid.uuid4()), str(uuid.uuid4())
    cc1, cc2, cc3 = (str(uuid.uuid4()) for _ in range(3))
    seen = {"scope_token": None, "asked": []}

    async def _scope(token):
        seen["scope_token"] = token
        return {"full_access": False, "cost_centers": [
            {"id": cc1, "department_id": dept_a}, {"id": cc2, "department_id": dept_b},
            {"id": cc3, "department_id": dept_b}]}

    by_cc = {
        # Travel: 1,200 of 1,000 here …
        cc1: [{"l1_code": "OPEX", "annual_budget": "1000", "committed": "0", "actual_spent": "1200"}],
        # … and 100 of 1,000 here: summed it would be 1,300 of 2,000 — not over.
        cc2: [{"l1_code": "OPEX", "annual_budget": "1000", "committed": "0", "actual_spent": "100"},
              {"l1_code": "CAPEX", "annual_budget": "0", "committed": "0", "actual_spent": "50"}],
        cc3: [{"l1_code": "IDLE", "annual_budget": "0", "committed": "0", "actual_spent": "0"}],
    }

    async def _summary(token, fiscal_year, cost_center_id=None):
        seen["asked"].append(str(cost_center_id))
        return {"accounts": by_cc[str(cost_center_id)]}

    monkeypatch.setattr(budget_client, "get_actuals_scope", _scope)
    monkeypatch.setattr(budget_client, "get_actuals_summary", _summary)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        mgr = await _seed(db)
        board = await dash_crud.build(db, "dept_manager", mgr.id,
                                      user={"sub": str(mgr.id), "role": "dept_manager"}, token="tok")
    assert seen["scope_token"] == "tok" and sorted(seen["asked"]) == sorted([cc1, cc2, cc3])
    card = _kpi(board, "Over-Budget Depts")
    # Over: cc1 Travel (120%), cc2 CAPEX (spend, no budget). Departments: A and B.
    assert card.value == "2" and card.subtitle == "2 accounts over budget"
    groups = {g.l1_code: g for g in board.budget_overview.groups}
    assert set(groups) == {"OPEX", "CAPEX"}
    assert groups["OPEX"].annual_budget == Decimal("2000") and groups["OPEX"].actual_spent == Decimal("1300")
    assert groups["OPEX"].utilisation_pct == 65.0


async def test_budget_overview_is_empty_when_budget_api_is_down(test_engine, monkeypatch):
    from app.services import budget_client

    monkeypatch.setattr(dash_crud, "_cc_summary_cache", {})

    async def _down(token):
        return None
    monkeypatch.setattr(budget_client, "get_actuals_scope", _down)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        mgr = await _seed(db)
        board = await dash_crud.build(db, "dept_manager", mgr.id,
                                      user={"sub": str(mgr.id), "role": "dept_manager"}, token="tok")
    assert _kpi(board, "Over-Budget Depts").value == "0"
    assert board.budget_overview.groups == []
