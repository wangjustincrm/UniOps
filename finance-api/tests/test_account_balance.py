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
    assert by["MOH-01"]["amount"] == "100.00"
    assert by["MOH-02"]["amount"] == "50.00"
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
    rows = {tuple((k["dim_code"], k["code"]) for k in r["keys"]): r["amount"]
            for r in exp["rows"]}
    assert rows[(("cost_center", "MOH-01"), ("income_expense_item", "CRM004"))] == "100.00"
    assert rows[(("cost_center", "MOH-01"), ("income_expense_item", None))] == "40.00"
    # name resolution
    named = next(r for r in exp["rows"]
                 if r["keys"][1]["code"] == "CRM004")
    assert named["keys"][1]["name"] == "Depreciation"


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
    assert [d["dim_code"] for d in dims] == ["cost_center", "supplier"]
    assert dims[0]["supported"] is True and dims[1]["supported"] is False
    # unconfigured account falls back to the full supported registry
    r2 = await client.get("/finance/v1/gl/account-balance/9999/dims", headers=_h())
    assert {d["dim_code"] for d in r2.json()["dims"]} == {
        "cost_center", "department", "income_expense_item"}


async def test_expand_endpoint_dims_param(client, db_session):
    cc = await _cc(db_session, "MOH-01")
    await _posted_cc_event(db_session, "5101", cc, "60.00")
    await jv_crud.backfill_posted_jvs(db_session)
    r = await client.get(
        "/finance/v1/gl/account-balance/5101/expand?period=2026-07&dims=cost_center",
        headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["rows"][0]["amount"] == "60.00"
    r422 = await client.get(
        "/finance/v1/gl/account-balance/5101/expand?period=2026-07&dims=bananas",
        headers=_h())
    assert r422.status_code == 422
