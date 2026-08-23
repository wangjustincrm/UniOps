"""Bank import, reconciliation matching, FX (Phase a A3)."""
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.bank import BankAccount, BankTransaction
from app.models.payment import PaymentRecord


def _token(role="finance_manager"):
    return jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h(role="finance_manager"):
    return {"Authorization": f"Bearer {_token(role)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _account(db_session, currency="CAD") -> BankAccount:
    acct = BankAccount(name="Operating", bank_name="RBC", account_masked="1234",
                       currency=currency, ledger_account_code="1010")
    db_session.add(acct)
    await db_session.flush()
    return acct


def _pay(amount, payment_date, currency="CAD", doc_number=None, reference=None) -> PaymentRecord:
    return PaymentRecord(
        doc_kind="pa_dir", doc_id=uuid.uuid4(), doc_number=doc_number,
        payment_date=payment_date, payment_method="bank_transfer",
        reference=reference, amount=Decimal(amount), currency=currency,
        recorded_by=uuid.uuid4(), status="completed",
    )


# ── import ──────────────────────────────────────────────────────────────────────

async def test_import_dedups_within_and_across_batches(client, db_session):
    acct = await _account(db_session)
    csv1 = ("date,amount,description,reference\n"
            "2026-06-01,-500.00,EFT ACME,TXN1\n"
            "2026-06-02,1200.00,Customer deposit,\n"
            "2026-06-01,-500.00,EFT ACME,TXN1\n")  # dup of row 1
    r = await client.post(f"/finance/v1/bank/{acct.id}/import", headers=_h(),
                          files={"file": ("s.csv", csv1.encode("utf-8-sig"), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json() == {"imported": 2, "duplicates": 1, "skipped": 0, "errors": []}

    # re-import same file → all 3 lines are duplicates (2 hit the DB, 1 in-batch)
    r2 = await client.post(f"/finance/v1/bank/{acct.id}/import", headers=_h(),
                           files={"file": ("s.csv", csv1.encode("utf-8-sig"), "text/csv")})
    assert r2.json()["imported"] == 0 and r2.json()["duplicates"] == 3


async def test_import_reports_bad_rows(client, db_session):
    acct = await _account(db_session)
    csv = ("date,amount,description\n"
           "2026-06-01,-100.00,Good\n"
           "notadate,-50.00,Bad date\n")
    r = await client.post(f"/finance/v1/bank/{acct.id}/import", headers=_h(),
                          files={"file": ("s.csv", csv.encode("utf-8-sig"), "text/csv")})
    body = r.json()
    assert body["imported"] == 1 and len(body["errors"]) == 1


# ── auto match ──────────────────────────────────────────────────────────────────

async def test_auto_match_unique_candidate(client, db_session):
    acct = await _account(db_session)
    db_session.add(_pay("500.00", date(2026, 6, 1)))
    db_session.add(BankTransaction(
        bank_account_id=acct.id, txn_date=date(2026, 6, 2), description="EFT",
        amount=Decimal("-500.00"), currency="CAD", import_hash="h1"))
    await db_session.flush()

    r = await client.post("/finance/v1/bank/match/auto", headers=_h(),
                          params={"account_id": str(acct.id)})
    assert r.status_code == 200
    assert r.json()["matched"] == 1
    txn = (await db_session.execute(select(BankTransaction))).scalars().first()
    assert txn.status == "matched" and txn.matched_payment_id is not None


async def test_auto_match_ambiguous_left_for_human(client, db_session):
    acct = await _account(db_session)
    db_session.add(_pay("300.00", date(2026, 6, 1)))
    db_session.add(_pay("300.00", date(2026, 6, 1)))   # two identical-amount payments
    db_session.add(BankTransaction(
        bank_account_id=acct.id, txn_date=date(2026, 6, 2), description="EFT",
        amount=Decimal("-300.00"), currency="CAD", import_hash="h2"))
    await db_session.flush()

    r = await client.post("/finance/v1/bank/match/auto", headers=_h(),
                          params={"account_id": str(acct.id)})
    assert r.json()["matched"] == 0 and r.json()["ambiguous"] == 1


async def test_auto_match_disambiguates_by_reference(client, db_session):
    acct = await _account(db_session)
    db_session.add(_pay("300.00", date(2026, 6, 1), doc_number="PA-20260601-0007"))
    db_session.add(_pay("300.00", date(2026, 6, 1), doc_number="PA-20260601-0008"))
    db_session.add(BankTransaction(
        bank_account_id=acct.id, txn_date=date(2026, 6, 2),
        description="Wire", reference="Payment PA-20260601-0007 vendor",
        amount=Decimal("-300.00"), currency="CAD", import_hash="h3"))
    await db_session.flush()

    r = await client.post("/finance/v1/bank/match/auto", headers=_h(),
                          params={"account_id": str(acct.id)})
    assert r.json()["matched"] == 1


# ── manual match / unmatch ───────────────────────────────────────────────────────

async def test_manual_match_allows_amount_mismatch(client, db_session):
    acct = await _account(db_session)
    pay = _pay("500.00", date(2026, 6, 1))
    db_session.add(pay)
    txn = BankTransaction(bank_account_id=acct.id, txn_date=date(2026, 6, 2),
                          description="EFT less bank fee", amount=Decimal("-498.00"),
                          currency="CAD", import_hash="h4")
    db_session.add(txn)
    await db_session.flush()

    r = await client.post(f"/finance/v1/bank/transactions/{txn.id}/match", headers=_h(),
                          json={"payment_record_id": str(pay.id)})
    assert r.status_code == 200 and r.json()["status"] == "matched"

    # cannot double-claim the same payment for a different line
    txn2 = BankTransaction(bank_account_id=acct.id, txn_date=date(2026, 6, 3),
                           description="dup", amount=Decimal("-500.00"),
                           currency="CAD", import_hash="h5")
    db_session.add(txn2)
    await db_session.flush()
    r2 = await client.post(f"/finance/v1/bank/transactions/{txn2.id}/match", headers=_h(),
                           json={"payment_record_id": str(pay.id)})
    assert r2.status_code == 409

    # unmatch frees it
    r3 = await client.post(f"/finance/v1/bank/transactions/{txn.id}/unmatch", headers=_h())
    assert r3.status_code == 200 and r3.json()["status"] == "unmatched"


# ── reconciliation summary ───────────────────────────────────────────────────────

async def test_reconciliation_summary(client, db_session):
    acct = await _account(db_session)
    db_session.add_all([
        BankTransaction(bank_account_id=acct.id, txn_date=date(2026, 6, 1),
                        description="in", amount=Decimal("1000.00"), currency="CAD",
                        status="unmatched", import_hash="r1"),
        BankTransaction(bank_account_id=acct.id, txn_date=date(2026, 6, 2),
                        description="out", amount=Decimal("-400.00"), currency="CAD",
                        status="matched", import_hash="r2"),
    ])
    db_session.add(_pay("999.00", date(2026, 6, 2)))   # unreconciled payment
    await db_session.flush()

    r = await client.get("/finance/v1/bank/reconciliation", headers=_h(),
                         params={"account_id": str(acct.id)})
    b = r.json()
    assert b["inflow"] == "1000.00" and b["outflow"] == "-400.00"
    assert b["unmatched_count"] == 1
    assert any(p["amount"] == "999.00" for p in b["unreconciled_payments"])


# ── FX ───────────────────────────────────────────────────────────────────────────

async def test_fx_rate_lookup_uses_latest_on_or_before(client, db_session):
    await client.post("/finance/v1/bank/rates", headers=_h(), json={
        "from_currency": "USD", "rate": "1.35", "effective_date": "2026-06-01"})
    await client.post("/finance/v1/bank/rates", headers=_h(), json={
        "from_currency": "USD", "rate": "1.37", "effective_date": "2026-06-10"})
    from app.crud.bank import rate_on
    assert await rate_on(db_session, "USD", date(2026, 6, 5)) == Decimal("1.35000000")
    assert await rate_on(db_session, "USD", date(2026, 6, 12)) == Decimal("1.37000000")
    assert await rate_on(db_session, "CAD", date(2026, 6, 12)) == Decimal("1")
    assert await rate_on(db_session, "EUR", date(2026, 6, 12)) is None


async def test_payment_locks_fx_rate_on_posting(client, db_session):
    """USD PA payment stamps the posting lines with the effective USD→CAD rate."""
    from app.models.pa import PaymentApplication
    from app.models.posting import PostingEvent, PostingLine

    await client.post("/finance/v1/bank/rates", headers=_h(), json={
        "from_currency": "USD", "rate": "1.36", "effective_date": "2026-06-01"})
    pa = PaymentApplication(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="USD pay", pa_type="PA-DIR",
        status="approved", po_id=None, po_number=None, vendor_id=uuid.uuid4(),
        vendor_name="US Vendor", invoice_ids=[], payment_amount=Decimal("100.00"),
        currency="USD", created_by=uuid.uuid4())
    db_session.add(pa)
    await db_session.flush()

    r = await client.post("/finance/v1/payments/execute", headers=_h("payment_officer"),
                          json={"doc_kind": "pa_dir", "doc_id": str(pa.id),
                                "payment_date": "2026-06-05"})
    assert r.status_code == 200, r.text
    ev = (await db_session.execute(
        select(PostingEvent).where(PostingEvent.source_doc_id == pa.id))).scalar_one()
    lines = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev.id))).scalars().all()
    assert all(ln.fx_rate == Decimal("1.360000") for ln in lines)
