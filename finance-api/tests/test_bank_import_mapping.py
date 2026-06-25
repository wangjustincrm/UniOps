"""Bank statement import via column mapping + exclude action (A3 workbench)."""
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.crud import bank as bank_crud
from app.db.base import get_db
from app.main import app
from app.models.bank import EXCLUDED, UNMATCHED, BankAccount, BankTransaction


def _h(role="finance_manager"):
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _account(db_session, currency="CAD") -> BankAccount:
    acct = BankAccount(name="Operating", bank_name="RBC", currency=currency)
    db_session.add(acct)
    await db_session.flush()
    return acct


# ── debit/credit split (BoC / RBC layout) ───────────────────────────────────────

async def test_debit_credit_split_signs(db_session):
    acct = await _account(db_session)
    csv = (
        "Date,Description,Debit Amount,Credit Amount\n"
        "2026-05-06,Wire to vendor,\"83,748.00\",\n"
        "2026-05-11,LOBLAW INC deposit,,\"131,423.20\"\n"
        "2026-05-01,Balance carry,,\n"  # zero net → skipped
    )
    m = {"date": "Date", "description": "Description",
         "debit": "Debit Amount", "credit": "Credit Amount"}
    res = await bank_crud.import_statement_csv(db_session, acct, csv, mapping=m)
    assert res["imported"] == 2 and res["skipped"] == 1 and not res["errors"]
    rows = {r.description: r.amount for r in (await db_session.execute(
        select(BankTransaction).where(BankTransaction.bank_account_id == acct.id))).scalars()}
    assert rows["Wire to vendor"] == Decimal("-83748.00")      # debit = outflow
    assert rows["LOBLAW INC deposit"] == Decimal("131423.20")  # credit = inflow


async def test_rbc_default_year_and_money_formats(db_session):
    """RBC: date '01 May' (no year), parens negatives, $ and commas."""
    acct = await _account(db_session)
    csv = (
        "Date,Description,Amount\n"
        "01 May,PAY-FILE FEES,($2.00)\n"
        "06 May,Funds transfer credit,\"$300,000.00\"\n"
    )
    m = {"date": "Date", "description": "Description", "amount": "Amount",
         "date_format": "%d %b", "default_year": 2026}
    res = await bank_crud.import_statement_csv(db_session, acct, csv, mapping=m)
    assert res["imported"] == 2, res
    rows = {r.txn_date: r.amount for r in (await db_session.execute(
        select(BankTransaction).where(BankTransaction.bank_account_id == acct.id))).scalars()}
    assert rows[date(2026, 5, 1)] == Decimal("-2.00")
    assert rows[date(2026, 5, 6)] == Decimal("300000.00")


async def test_mmddyy_date_format(db_session):
    acct = await _account(db_session)
    csv = "TrD,Detail,Dr,Cr\n05/04/26,Property Tax,237655.00,\n"
    m = {"date": "TrD", "description": "Detail", "debit": "Dr", "credit": "Cr",
         "date_format": "%m/%d/%y"}
    res = await bank_crud.import_statement_csv(db_session, acct, csv, mapping=m)
    assert res["imported"] == 1
    row = (await db_session.execute(
        select(BankTransaction).where(BankTransaction.bank_account_id == acct.id))).scalar_one()
    assert row.txn_date == date(2026, 5, 4) and row.amount == Decimal("-237655.00")


async def test_mapping_missing_column_errors(db_session):
    acct = await _account(db_session)
    csv = "Date,Description,Amount\n2026-05-01,x,1.00\n"
    m = {"date": "Date", "description": "Description", "amount": "NotThere"}
    res = await bank_crud.import_statement_csv(db_session, acct, csv, mapping=m)
    assert res["imported"] == 0 and res["errors"]


# ── endpoint: mapping form field persists preset + reuse ─────────────────────────

async def test_import_endpoint_saves_and_reuses_mapping(client, db_session):
    acct = await _account(db_session)
    import json
    m = {"date": "Date", "description": "Description",
         "debit": "Debit Amount", "credit": "Credit Amount"}
    csv1 = "Date,Description,Debit Amount,Credit Amount\n2026-05-06,Wire,100.00,\n"
    r = await client.post(f"/finance/v1/bank/{acct.id}/import", headers=_h(),
                          files={"file": ("s.csv", csv1, "text/csv")},
                          data={"mapping": json.dumps(m)})
    assert r.status_code == 200 and r.json()["imported"] == 1
    await db_session.refresh(acct)
    assert acct.import_mapping == m  # preset saved

    # second import without mapping reuses the saved preset
    csv2 = "Date,Description,Debit Amount,Credit Amount\n2026-05-07,Wire2,200.00,\n"
    r = await client.post(f"/finance/v1/bank/{acct.id}/import", headers=_h(),
                          files={"file": ("s.csv", csv2, "text/csv")})
    assert r.status_code == 200 and r.json()["imported"] == 1


# ── exclude action ───────────────────────────────────────────────────────────────

async def test_update_account_sets_gl_code(client, db_session):
    acct = await _account(db_session)
    r = await client.put(f"/finance/v1/bank/accounts/{acct.id}", headers=_h(),
                         json={"name": "RBC Operating", "bank_name": "Royal Bank of Canada",
                               "account_masked": "376-0", "currency": "CAD",
                               "ledger_account_code": "1010", "is_active": True})
    assert r.status_code == 200 and r.json()["ledger_account_code"] == "1010"


async def test_exclude_and_reinclude(client, db_session):
    acct = await _account(db_session)
    txn = BankTransaction(bank_account_id=acct.id, txn_date=date(2026, 5, 15),
                          description="Account Analysis Settlement Charge",
                          amount=Decimal("-148.29"), currency="CAD",
                          status=UNMATCHED, import_hash=uuid.uuid4().hex)
    db_session.add(txn)
    await db_session.flush()

    r = await client.post(f"/finance/v1/bank/transactions/{txn.id}/exclude",
                          headers=_h(), json={"excluded": True})
    assert r.status_code == 200 and r.json()["status"] == EXCLUDED

    # excluded line drops off the reconciliation discrepancy list
    r = await client.get("/finance/v1/bank/reconciliation", headers=_h(),
                         params={"account_id": str(acct.id)})
    assert r.json()["unmatched_count"] == 0

    r = await client.post(f"/finance/v1/bank/transactions/{txn.id}/exclude",
                          headers=_h(), json={"excluded": False})
    assert r.json()["status"] == UNMATCHED
