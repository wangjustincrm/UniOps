"""JV-based account balance — Plan 3 Task 2."""
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.crud import account_balance as ab
from app.crud import journal_voucher as jv_crud
from app.services.posting import emit_event


async def _posted_event(db, period_month, debit_acct="5000", amount="100.00"):
    """Emit an accrual then backfill-post it so it counts toward the JV balance.
    period_month like '2026-06' controls the JV fiscal_period via occurred_at."""
    from datetime import datetime, timezone
    occurred = datetime(int(period_month[:4]), int(period_month[5:7]), 15, tzinfo=timezone.utc)
    await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        occurred_at=occurred, prepared_by=uuid.uuid4(),
        lines=[{"line_role": "purchase_expense", "account_code": debit_acct,
                "debit": Decimal(amount), "currency": "CAD"},
               {"line_role": "accounts_payable", "account_code": "2000",
                "credit": Decimal(amount), "currency": "CAD"}])


async def test_account_balance_opening_movement_closing(db_session):
    await _posted_event(db_session, "2026-06", amount="100.00")   # prior period
    await _posted_event(db_session, "2026-07", amount="40.00")    # current period
    await jv_crud.backfill_posted_jvs(db_session)                 # post them all

    bal = await ab.account_balance(db_session, "2026-07")
    by_code = {r["account_code"]: r for r in bal["rows"]}
    # 5000 expense: opening 100 (debit), movement +40 debit, closing 140
    assert by_code["5000"]["opening"] == "100.00"
    assert by_code["5000"]["period_debit"] == "40.00"
    assert by_code["5000"]["closing"] == "140.00"
    # 2000 AP: opening -100 (credit), movement -40, closing -140
    assert by_code["2000"]["closing"] == "-140.00"
    assert bal["balanced"] is True


async def test_account_balance_excludes_draft(db_session):
    await _posted_event(db_session, "2026-07")   # left as draft (no backfill)
    bal = await ab.account_balance(db_session, "2026-07")
    assert bal["rows"] == []                     # draft JVs don't count


from datetime import datetime, timezone, timedelta
from httpx import ASGITransport, AsyncClient
from jose import jwt
from app.core.config import settings
from app.db.base import get_db
from app.main import app


def _h():
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": "finance_manager",
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override():
        yield db_session
    app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_account_balance_endpoint(client, db_session):
    await _posted_event(db_session, "2026-07", amount="55.00")
    await jv_crud.backfill_posted_jvs(db_session)
    r = await client.get("/finance/v1/gl/account-balance?period=2026-07", headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    by_code = {row["account_code"]: row for row in body["rows"]}
    assert by_code["5000"]["closing"] == "55.00"
    assert body["balanced"] is True


# ── ②③④ cost-center expansion / drill-down / Budget Actual ──────────────────────
from app.models.mirrors import CostCenter


async def _cc(db, code, name="cc"):
    cid = uuid.uuid4()
    db.add(CostCenter(id=cid, code=code, name=name))
    await db.flush()
    return cid


async def _posted_cc_event(db, account, cc_id, amount, period="2026-07"):
    occurred = datetime(int(period[:4]), int(period[5:7]), 15, tzinfo=timezone.utc)
    await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        occurred_at=occurred, prepared_by=uuid.uuid4(),
        lines=[{"line_role": "purchase_expense", "account_code": account,
                "debit": Decimal(amount), "currency": "CAD", "cost_center_id": cc_id},
               {"line_role": "accounts_payable", "account_code": "2000",
                "credit": Decimal(amount), "currency": "CAD"}])


async def test_budget_actual_by_cost_center(db_session):
    moh = await _cc(db_session, "MOH-01", "Line 1")
    await _posted_cc_event(db_session, "5101", moh, "300.00")
    await jv_crud.backfill_posted_jvs(db_session)
    ba = await ab.budget_actual(db_session, "2026-07")
    rows = [r for r in ba["rows"] if r["account_code"] == "5101"]
    assert len(rows) == 1
    assert rows[0]["category"] == "MOH"
    assert rows[0]["cost_center_code"] == "MOH-01"
    assert rows[0]["actual"] == "300.00"


async def test_expand_single_dim_matches_old_behavior(db_session):
    cc1 = await _cc(db_session, "MOH-01")
    cc2 = await _cc(db_session, "MOH-02")
    await _posted_cc_event(db_session, "5101", cc1, "100.00")
    await _posted_cc_event(db_session, "5101", cc2, "50.00")
    await jv_crud.backfill_posted_jvs(db_session)
    exp = await ab.expand_by_dims(db_session, "5101", "2026-07", ["cost_center"])
    by = {r["keys"][0]["code"]: r for r in exp["rows"]}
    assert by["MOH-01"]["closing"] == "100.00"
    assert by["MOH-02"]["closing"] == "50.00"
    assert by["MOH-01"]["keys"][0]["dim_code"] == "cost_center"


async def test_account_vouchers_drilldown_by_dims(db_session):
    cc1 = await _cc(db_session, "MOH-01")
    await _posted_cc_event(db_session, "5101", cc1, "77.00")
    await jv_crud.backfill_posted_jvs(db_session)
    v = await ab.account_vouchers(db_session, "5101", "2026-07",
                                  dims_values={"cost_center": cc1})
    assert len(v["rows"]) == 1
    assert v["rows"][0]["local_debit"] == "77.00"
    assert v["rows"][0]["jv_number"].startswith("JV-")


async def test_budget_actual_endpoint(client, db_session):
    moh = await _cc(db_session, "MOH-01")
    await _posted_cc_event(db_session, "5101", moh, "42.00")
    await jv_crud.backfill_posted_jvs(db_session)
    r = await client.get("/finance/v1/gl/budget-actual?period=2026-07", headers=_h())
    assert r.status_code == 200, r.text
    rows = [x for x in r.json()["rows"] if x["account_code"] == "5101"]
    assert rows[0]["actual"] == "42.00"
    assert rows[0]["category"] == "MOH"


# ── multi-dim expand foundations (Task 1) ─────────────────────────────────────────

async def test_jv_line_income_expense_item_column(db_session):
    from app.models.journal_voucher import JournalVoucher, JournalVoucherLine
    from datetime import date
    ba = uuid.uuid4()
    jv = JournalVoucher(jv_number="JV-209901-0001", voucher_word="JV",
                        voucher_date=date(2099, 1, 1), fiscal_period="2099-01",
                        status="posted")
    db_session.add(jv); await db_session.flush()
    ln = JournalVoucherLine(jv_id=jv.id, line_no=1, account_code="5101",
                            local_debit=Decimal("1.00"), currency="CAD",
                            fx_rate=Decimal("1"), income_expense_item_id=ba)
    db_session.add(ln); await db_session.flush()
    got = (await db_session.execute(select(JournalVoucherLine).where(
        JournalVoucherLine.id == ln.id))).scalar_one()
    assert got.income_expense_item_id == ba


async def test_coa_aux_item_roundtrip_and_unique(db_session):
    from sqlalchemy.exc import IntegrityError
    from app.models.coa import CoaAuxItem
    db_session.add(CoaAuxItem(account_code="5101", dim_code="cost_center", seq=1))
    await db_session.flush()
    db_session.add(CoaAuxItem(account_code="5101", dim_code="cost_center", seq=2))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


# ── generic multi-dim expansion (Task 3) ──────────────────────────────────────────
from app.models.mirrors import BudgetAccount, Department


async def _posted_dim_event(db, account, amount, cc_id=None, dept_id=None, ba_id=None,
                            period="2026-07"):
    occurred = datetime(int(period[:4]), int(period[5:7]), 15, tzinfo=timezone.utc)
    line = {"line_role": "purchase_expense", "account_code": account,
            "debit": Decimal(amount), "currency": "CAD"}
    if cc_id:
        line["cost_center_id"] = cc_id
    if dept_id:
        line["department_id"] = dept_id
    if ba_id:
        line["aux"] = {"income_expense_item": {"value_id": ba_id, "value_text": "X"}}
    await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        occurred_at=occurred, prepared_by=uuid.uuid4(),
        lines=[line, {"line_role": "accounts_payable", "account_code": "2000",
                      "credit": Decimal(amount), "currency": "CAD"}])


async def test_expand_two_dims_and_none_group(db_session):
    cc = await _cc(db_session, "MOH-01")
    ba = uuid.uuid4()
    db_session.add(BudgetAccount(id=ba, code="CRM004", name="Depreciation", is_active=True))
    await db_session.flush()
    await _posted_dim_event(db_session, "5101", "100.00", cc_id=cc, ba_id=ba)
    await _posted_dim_event(db_session, "5101", "40.00", cc_id=cc)          # no ioitem
    await jv_crud.backfill_posted_jvs(db_session)
    exp = await ab.expand_by_dims(db_session, "5101", "2026-07",
                                  ["cost_center", "income_expense_item"])
    assert len(exp["rows"]) == 2
    rows = {tuple((k["dim_code"], k["code"]) for k in r["keys"]): r["closing"]
            for r in exp["rows"]}
    assert rows[(("cost_center", "MOH-01"), ("income_expense_item", "CRM004"))] == "100.00"
    assert rows[(("cost_center", "MOH-01"), ("income_expense_item", None))] == "40.00"
    # name resolution
    named = next(r for r in exp["rows"]
                 if r["keys"][1]["code"] == "CRM004")
    assert named["keys"][1]["name"] == "Depreciation"


async def _credit_dim_event(db, account, amount, dept_id=None, period="2026-07"):
    """Same shape as _posted_dim_event but credits `account` (paired debit on
    5000) — used to seed a credit-side balance on the account under test."""
    occurred = datetime(int(period[:4]), int(period[5:7]), 15, tzinfo=timezone.utc)
    line = {"line_role": "accounts_payable", "account_code": account,
            "credit": Decimal(amount), "currency": "CAD"}
    if dept_id:
        line["department_id"] = dept_id
    await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        occurred_at=occurred, prepared_by=uuid.uuid4(),
        lines=[{"line_role": "purchase_expense", "account_code": "5000",
                "debit": Decimal(amount), "currency": "CAD"}, line])


async def test_expand_shows_only_dims_with_this_period_movement(db_session):
    # Monthly view (user decision 2026-07-17): the expansion lists only dimensions
    # that MOVED this period. A dimension with only a carried-forward opening and
    # no current-period line does NOT appear — otherwise its row shows a balance
    # but its (per-period) Vouchers drill is empty, which reads as broken.
    # dept A: prior debit 100 (opening); this period debit 30, credit 50 -> appears
    # dept B: only a prior-period credit 40, no this-period line -> excluded
    ACCT, PERIOD = "5101", "2026-07"
    dept_a, dept_b = uuid.uuid4(), uuid.uuid4()
    db_session.add_all([
        Department(id=dept_a, code="A", name="Dept A"),
        Department(id=dept_b, code="B", name="Dept B"),
    ])
    await db_session.flush()

    await _posted_dim_event(db_session, ACCT, "100.00", dept_id=dept_a, period="2026-06")
    await _posted_dim_event(db_session, ACCT, "30.00", dept_id=dept_a, period=PERIOD)
    await _credit_dim_event(db_session, ACCT, "50.00", dept_id=dept_a, period=PERIOD)
    await _credit_dim_event(db_session, ACCT, "40.00", dept_id=dept_b, period="2026-06")
    await jv_crud.backfill_posted_jvs(db_session)

    out = await ab.expand_by_dims(db_session, account_code=ACCT, period=PERIOD, dims=["department"])
    by = {r["keys"][0]["code"]: r for r in out["rows"]}
    # dept A moved this period -> appears, still with its carried opening + closing
    assert by["A"]["opening"] == "100.00"
    assert by["A"]["period_debit"] == "30.00" and by["A"]["period_credit"] == "50.00"
    assert by["A"]["closing"] == "80.00"          # 100 + 30 - 50
    # dept B had no this-period movement -> excluded (this is the fix)
    assert "B" not in by

    # Reconciliation is on THIS PERIOD's movement (the monthly view): the shown
    # children's period debit/credit sum to the parent account row's period
    # debit/credit.
    ab_report = await ab.account_balance(db_session, PERIOD)
    parent = next(r for r in ab_report["rows"] if r["account_code"] == ACCT)
    assert sum(Decimal(r["period_debit"]) for r in out["rows"]) == Decimal(parent["period_debit"])
    assert sum(Decimal(r["period_credit"]) for r in out["rows"]) == Decimal(parent["period_credit"])


async def test_expand_rejects_unknown_dim(db_session):
    with pytest.raises(ab.BadDims):
        await ab.expand_by_dims(db_session, "5101", "2026-07", ["bananas"])


async def test_expand_rejects_duplicate_dims(db_session):
    with pytest.raises(ab.BadDims):
        await ab.expand_by_dims(db_session, "5101", "2026-07",
                                ["cost_center", "cost_center"])


async def test_vouchers_filter_none_and_combo(db_session):
    cc = await _cc(db_session, "MOH-01")
    ba = uuid.uuid4()
    await _posted_dim_event(db_session, "5101", "100.00", cc_id=cc, ba_id=ba)
    await _posted_dim_event(db_session, "5101", "40.00", cc_id=cc)
    await jv_crud.backfill_posted_jvs(db_session)
    v_none = await ab.account_vouchers(db_session, "5101", "2026-07",
                                       dims_values={"cost_center": cc,
                                                    "income_expense_item": None})
    assert [r["local_debit"] for r in v_none["rows"]] == ["40.00"]
    v_hit = await ab.account_vouchers(db_session, "5101", "2026-07",
                                      dims_values={"income_expense_item": ba})
    assert [r["local_debit"] for r in v_hit["rows"]] == ["100.00"]


async def test_dims_endpoint_config_and_fallback(client, db_session):
    from app.models.coa import CoaAuxItem
    db_session.add_all([
        CoaAuxItem(account_code="5101", dim_code="cost_center", seq=1),
        CoaAuxItem(account_code="5101", dim_code="supplier", seq=2),
    ])
    await db_session.flush()
    r = await client.get("/finance/v1/gl/account-balance/5101/dims", headers=_h())
    dims = r.json()["dims"]
    assert [d["dim_code"] for d in dims] == [
        "cost_center", "supplier", "department", "income_expense_item", "customer", "partner"]
    assert all(d["supported"] is True for d in dims)
    # unconfigured account falls back to the full supported registry
    r2 = await client.get("/finance/v1/gl/account-balance/9999/dims", headers=_h())
    assert {d["dim_code"] for d in r2.json()["dims"]} == {
        "cost_center", "department", "income_expense_item", "supplier", "customer", "partner"}


async def test_expand_endpoint_dims_param(client, db_session):
    cc = await _cc(db_session, "MOH-01")
    await _posted_cc_event(db_session, "5101", cc, "60.00")
    await jv_crud.backfill_posted_jvs(db_session)
    r = await client.get(
        "/finance/v1/gl/account-balance/5101/expand?period=2026-07&dims=cost_center",
        headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["rows"][0]["closing"] == "60.00"
    r422 = await client.get(
        "/finance/v1/gl/account-balance/5101/expand?period=2026-07&dims=bananas",
        headers=_h())
    assert r422.status_code == 422


def test_aux_item_name_mapping():
    import importlib.util, os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "nc_migration", "aux_items_import.py")
    spec = importlib.util.spec_from_file_location("aux_items_import", p)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    f = mod.map_assitem_name
    assert f("部门", "bm") == "department"
    assert f("成本中心", "cbzx") == "cost_center"
    assert f("收支项目", "szxm") == "income_expense_item"
    assert f("供应商", "gys") == "supplier"
    assert f("客户", "kh") == "customer"
    assert f("神秘档案", "SomeCode") == "somecode"     # unknown -> code slug
    assert f("神秘档案", "神秘") == "unknown"           # empty-slug fallback


# ── partner dimensions foundations ────────────────────────────────────────────

async def test_nc_customer_roundtrip_and_unique(db_session):
    from sqlalchemy.exc import IntegrityError
    from app.models.nc_customer import NcCustomer
    db_session.add(NcCustomer(code="CRM027", name="Debang Duoling", is_active=True))
    await db_session.flush()
    db_session.add(NcCustomer(code="CRM027", name="dup", is_active=True))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_erp_supplier_mirror_readable(db_session):
    from app.models.mirrors import ErpSupplier
    sid = uuid.uuid4()
    db_session.add(ErpSupplier(id=sid, erp_supplier_code="S001", supplier_name="ACME"))
    await db_session.flush()
    got = (await db_session.execute(select(ErpSupplier).where(
        ErpSupplier.id == sid))).scalar_one()
    assert got.supplier_name == "ACME"


def test_customer_pick_name():
    import importlib.util, os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "nc_migration", "customers_import.py")
    spec = importlib.util.spec_from_file_location("customers_import", p)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod.pick_name("Acme Ltd", "阿克梅", "C1") == "Acme Ltd"
    assert mod.pick_name(None, "阿克梅", "C1") == "阿克梅"
    assert mod.pick_name(" ", "", "C1") == "C1"


async def test_expand_by_supplier_and_customer(db_session):
    from app.models.mirrors import ErpSupplier
    from app.models.nc_customer import NcCustomer
    sup_id, cust_id, ghost = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(ErpSupplier(id=sup_id, erp_supplier_code="S001", supplier_name="ACME"))
    db_session.add(NcCustomer(id=cust_id, code="CRM027", name="Debang", is_active=True))
    await db_session.flush()

    async def _ev(account, amount, pid):
        occurred = datetime(2026, 7, 15, tzinfo=timezone.utc)
        await emit_event(
            db_session, source_service="finance", source_doc_type="ap_invoice",
            source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
            occurred_at=occurred, prepared_by=uuid.uuid4(),
            lines=[{"line_role": "purchase_expense", "account_code": "5000",
                    "debit": Decimal(amount), "currency": "CAD"},
                   {"line_role": "accounts_payable", "account_code": account,
                    "credit": Decimal(amount), "currency": "CAD", "partner_id": pid}])

    await _ev("2202", "100.00", sup_id)
    await _ev("2202", "40.00", ghost)          # no master row -> (unknown)
    await jv_crud.backfill_posted_jvs(db_session)

    exp = await ab.expand_by_dims(db_session, "2202", "2026-07", ["supplier"])
    by_id = {r["keys"][0]["id"]: r for r in exp["rows"]}
    assert by_id[str(sup_id)]["keys"][0]["code"] == "S001"
    assert by_id[str(sup_id)]["keys"][0]["name"] == "ACME"
    assert by_id[str(ghost)]["keys"][0]["code"] is None      # unknown master

    exp_c = await ab.expand_by_dims(db_session, "2202", "2026-07", ["customer"])
    assert str(cust_id) not in {r["keys"][0]["id"] for r in exp_c["rows"]}  # no data yet


# ── partner dim = supplier ∪ customer (Task 7) ─────────────────────────────────

async def test_partner_dim_is_supported(db_session):
    from app.crud.account_balance import _dimensions
    assert "partner" in _dimensions()


async def test_partner_dim_resolves_supplier_and_customer_together(db_session):
    # partner = supplier ∪ customer: both masters resolve through the one
    # partner_id column, each row carrying its own master's code/name.
    from app.models.mirrors import ErpSupplier
    from app.models.nc_customer import NcCustomer
    sup_id, cust_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(ErpSupplier(id=sup_id, erp_supplier_code="S001", supplier_name="ACME"))
    db_session.add(NcCustomer(id=cust_id, code="CRM027", name="Debang", is_active=True))
    await db_session.flush()

    async def _ev(amount, pid):
        occurred = datetime(2026, 7, 15, tzinfo=timezone.utc)
        await emit_event(
            db_session, source_service="finance", source_doc_type="ap_invoice",
            source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
            occurred_at=occurred, prepared_by=uuid.uuid4(),
            lines=[{"line_role": "purchase_expense", "account_code": "5000",
                    "debit": Decimal(amount), "currency": "CAD"},
                   {"line_role": "accounts_payable", "account_code": "2202",
                    "credit": Decimal(amount), "currency": "CAD", "partner_id": pid}])

    await _ev("100.00", sup_id)
    await _ev("40.00", cust_id)
    await jv_crud.backfill_posted_jvs(db_session)

    out = await ab.expand_by_dims(db_session, "2202", "2026-07", ["partner"])
    names = {k["name"] for r in out["rows"] for k in r["keys"]}
    assert names == {"ACME", "Debang"}


async def test_partner_dim_falls_back_to_line_name_when_master_is_gone(db_session):
    # 627 partner_ids in dev resolve against neither master — their master row
    # is gone. The name is denormalized on the line; use it rather than blank.
    ghost = uuid.uuid4()
    occurred = datetime(2026, 7, 15, tzinfo=timezone.utc)
    await emit_event(
        db_session, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        occurred_at=occurred, prepared_by=uuid.uuid4(),
        lines=[{"line_role": "purchase_expense", "account_code": "5000",
                "debit": Decimal("77.00"), "currency": "CAD"},
               {"line_role": "accounts_payable", "account_code": "2202",
                "credit": Decimal("77.00"), "currency": "CAD",
                "partner_id": ghost, "partner_name": "Ghost Vendor"}])
    await jv_crud.backfill_posted_jvs(db_session)

    out = await ab.expand_by_dims(db_session, "2202", "2026-07", ["partner"])
    key = out["rows"][0]["keys"][0]
    assert key["name"] == "Ghost Vendor"      # not None
    assert key["code"] is None                # no master row -> no code


async def test_partner_dim_orphan_name_fallback_is_deterministic(db_session):
    # Measured in dev data: one orphaned partner_id is denormalized under two
    # different partner_name values across jv_lines ("Jassbhatia Solutions" on
    # one line, "Best Buy" on another) -- NC data noise, not a bug. Without an
    # ORDER BY the resolved name would depend on DB row-return order and could
    # flip between runs. Assert it's pinned to the alphabetically-first name
    # and stable across repeated calls.
    ghost = uuid.uuid4()

    async def _ev(amount, pname):
        occurred = datetime(2026, 7, 15, tzinfo=timezone.utc)
        await emit_event(
            db_session, source_service="finance", source_doc_type="ap_invoice",
            source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
            occurred_at=occurred, prepared_by=uuid.uuid4(),
            lines=[{"line_role": "purchase_expense", "account_code": "5000",
                    "debit": Decimal(amount), "currency": "CAD"},
                   {"line_role": "accounts_payable", "account_code": "2202",
                    "credit": Decimal(amount), "currency": "CAD",
                    "partner_id": ghost, "partner_name": pname}])

    await _ev("50.00", "Jassbhatia Solutions")
    await _ev("30.00", "Best Buy")
    await jv_crud.backfill_posted_jvs(db_session)

    out1 = await ab.expand_by_dims(db_session, "2202", "2026-07", ["partner"])
    out2 = await ab.expand_by_dims(db_session, "2202", "2026-07", ["partner"])
    key1 = out1["rows"][0]["keys"][0]
    key2 = out2["rows"][0]["keys"][0]
    assert key1["name"] == "Best Buy"          # alphabetically first of the two
    assert key2["name"] == "Best Buy"          # stable across repeated calls


# ── non-leaf rollup: tree helpers (Task 1) ────────────────────────────────────
from types import SimpleNamespace
from app.crud.account_balance import (
    _children_index, _descendants, _level, _subtree_codes)


def _tree(*pairs):
    # pairs of (code, parent_code) -> {code: obj with .parent_code}
    return {code: SimpleNamespace(parent_code=parent) for code, parent in pairs}


def test_children_index_inverts_parent_code():
    coa = _tree(("6601", None), ("660101", "6601"), ("660102", "6601"))
    idx = _children_index(coa)
    assert sorted(idx["6601"]) == ["660101", "660102"]
    assert "660101" not in idx           # leaves have no children entry


def test_descendants_includes_self_and_all_levels():
    coa = _tree(("A", None), ("A1", "A"), ("A1a", "A1"), ("B", None))
    idx = _children_index(coa)
    assert _descendants(idx, "A") == {"A", "A1", "A1a"}
    assert _descendants(idx, "A1") == {"A1", "A1a"}
    assert _descendants(idx, "A1a") == {"A1a"}   # leaf -> just self
    assert _descendants(idx, "Z") == {"Z"}       # orphan not in tree -> just self


def test_descendants_cycle_guard_terminates():
    # a self/looping parent_code must not infinite-loop
    coa = _tree(("X", "Y"), ("Y", "X"))
    idx = _children_index(coa)
    assert _descendants(idx, "X") == {"X", "Y"}


def test_level_counts_depth_from_root():
    coa = _tree(("6601", None), ("660101", "6601"), ("66010101", "660101"))
    assert _level(coa, "6601") == 0
    assert _level(coa, "660101") == 1
    assert _level(coa, "66010101") == 2
    assert _level(coa, "orphan") == 0            # not in coa -> root level


def test_level_cycle_guard_terminates():
    coa = _tree(("X", "Y"), ("Y", "X"))
    assert _level(coa, "X") <= 2                 # returns, doesn't hang


async def test_subtree_codes_from_db(db_session):
    from app.models.coa import ChartOfAccount
    db_session.add_all([
        ChartOfAccount(code="6601", name="Selling", account_type="expense",
                       normal_balance="debit", is_postable=False, parent_code=None),
        ChartOfAccount(code="660101", name="Selling(fix)", account_type="expense",
                       normal_balance="debit", is_postable=True, parent_code="6601"),
        ChartOfAccount(code="660102", name="Selling(var)", account_type="expense",
                       normal_balance="debit", is_postable=True, parent_code="6601"),
    ])
    await db_session.flush()
    assert await _subtree_codes(db_session, "6601") == {"6601", "660101", "660102"}
    assert await _subtree_codes(db_session, "660101") == {"660101"}   # leaf
    assert await _subtree_codes(db_session, "9999") == {"9999"}       # orphan


# ── non-leaf rollup: main report (Task 2) ─────────────────────────────────────
async def _coa_selling_tree(db):
    from app.models.coa import ChartOfAccount
    db.add_all([
        ChartOfAccount(code="6601", name="Selling expenses", account_type="expense",
                       normal_balance="debit", is_postable=False, parent_code=None),
        ChartOfAccount(code="660101", name="Selling(fix)", account_type="expense",
                       normal_balance="debit", is_postable=True, parent_code="6601"),
        ChartOfAccount(code="660102", name="Selling(var)", account_type="expense",
                       normal_balance="debit", is_postable=True, parent_code="6601"),
    ])
    await db.flush()


async def test_account_balance_rolls_up_header(db_session):
    await _coa_selling_tree(db_session)
    await _posted_dim_event(db_session, "660101", "100.00", period="2026-07")
    await _posted_dim_event(db_session, "660102", "40.00", period="2026-07")
    await jv_crud.backfill_posted_jvs(db_session)

    bal = await ab.account_balance(db_session, "2026-07")
    by = {r["account_code"]: r for r in bal["rows"]}
    # header 6601 aggregates its two children (100 + 40)
    assert by["6601"]["period_debit"] == "140.00"
    assert by["6601"]["closing"] == "140.00"
    assert by["6601"]["is_postable"] is False and by["6601"]["level"] == 0
    # leaves unchanged, one level deeper
    assert by["660101"]["period_debit"] == "100.00" and by["660101"]["level"] == 1
    assert by["660102"]["period_debit"] == "40.00"


async def test_account_balance_totals_not_double_counted(db_session):
    # THE anti-double-count guarantee: the header row shows 140, but the grand
    # total counts each posted line once (140), not header+children (280).
    await _coa_selling_tree(db_session)
    await _posted_dim_event(db_session, "660101", "100.00", period="2026-07")
    await _posted_dim_event(db_session, "660102", "40.00", period="2026-07")
    await jv_crud.backfill_posted_jvs(db_session)

    bal = await ab.account_balance(db_session, "2026-07")
    assert bal["totals"]["period_debit"] == "140.00"     # not 280.00
    assert bal["balanced"] is True                       # 140 dr (children) == 140 cr (2000)


async def test_account_balance_leaf_only_unchanged(db_session):
    # orphan codes (not in COA) behave as leaves exactly as before rollup
    await _posted_event(db_session, "2026-06", amount="100.00")
    await _posted_event(db_session, "2026-07", amount="40.00")
    await jv_crud.backfill_posted_jvs(db_session)
    bal = await ab.account_balance(db_session, "2026-07")
    by = {r["account_code"]: r for r in bal["rows"]}
    assert by["5000"]["opening"] == "100.00"
    assert by["5000"]["closing"] == "140.00"
    assert by["5000"]["is_postable"] is True and by["5000"]["level"] == 0


# ── non-leaf rollup: expand / drill / budget-actual (Task 3) ───────────────────
async def test_expand_rolls_up_header_by_dim(db_session):
    await _coa_selling_tree(db_session)
    cc = await _cc(db_session, "MKT-01", "Marketing")
    await _posted_cc_event(db_session, "660101", cc, "100.00")
    await _posted_cc_event(db_session, "660102", cc, "40.00")
    await jv_crud.backfill_posted_jvs(db_session)
    exp = await ab.expand_by_dims(db_session, "6601", "2026-07", ["cost_center"])
    by = {r["keys"][0]["code"]: r for r in exp["rows"]}
    assert by["MKT-01"]["closing"] == "140.00"        # both children aggregated
    # leaf still isolates its own
    exp_leaf = await ab.expand_by_dims(db_session, "660101", "2026-07", ["cost_center"])
    assert {r["keys"][0]["code"]: r["closing"] for r in exp_leaf["rows"]} == {"MKT-01": "100.00"}


async def test_vouchers_header_drill_tags_child_account(db_session):
    await _coa_selling_tree(db_session)
    await _posted_dim_event(db_session, "660101", "100.00", period="2026-07")
    await _posted_dim_event(db_session, "660102", "40.00", period="2026-07")
    await jv_crud.backfill_posted_jvs(db_session)
    v = await ab.account_vouchers(db_session, "6601", "2026-07")
    codes = sorted(r["account_code"] for r in v["rows"])
    assert codes == ["660101", "660102"]
    names = {r["account_code"]: r["account_name"] for r in v["rows"]}
    assert names["660101"] == "Selling(fix)"


async def test_budget_actual_rolls_up_header(db_session):
    await _coa_selling_tree(db_session)               # 6601 is a BUDGET_ACTUAL account
    cc = await _cc(db_session, "SELL-01", "Sales")
    await _posted_cc_event(db_session, "660101", cc, "70.00")
    await jv_crud.backfill_posted_jvs(db_session)
    ba = await ab.budget_actual(db_session, "2026-07")
    rows = [r for r in ba["rows"] if r["account_code"] == "6601"]
    assert len(rows) == 1
    assert rows[0]["category"] == "SELL" and rows[0]["actual"] == "70.00"
