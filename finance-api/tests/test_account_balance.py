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


async def test_expand_by_cost_center(db_session):
    cc1 = await _cc(db_session, "MOH-01")
    cc2 = await _cc(db_session, "MOH-02")
    await _posted_cc_event(db_session, "5101", cc1, "100.00")
    await _posted_cc_event(db_session, "5101", cc2, "50.00")
    await jv_crud.backfill_posted_jvs(db_session)
    exp = await ab.expand_by_cost_center(db_session, "5101", "2026-07")
    by = {r["cost_center_code"]: r for r in exp["rows"]}
    assert by["MOH-01"]["amount"] == "100.00"
    assert by["MOH-02"]["amount"] == "50.00"


async def test_account_vouchers_drilldown(db_session):
    cc1 = await _cc(db_session, "MOH-01")
    await _posted_cc_event(db_session, "5101", cc1, "77.00")
    await jv_crud.backfill_posted_jvs(db_session)
    v = await ab.account_vouchers(db_session, "5101", "2026-07", cost_center_id=cc1)
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
