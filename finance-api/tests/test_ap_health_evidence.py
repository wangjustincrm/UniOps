"""AP Subledger Health: invoice-number search, JV clearings of unpaid bills,
and the same invoice payable in two currencies (services/nc_ap_bill_evidence)."""
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg2
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.services.nc_ap_bill_evidence import invoice_tokens

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
             f"user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")


def _exec(sql, params=()):
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor(); cur.execute(sql, params)
    con.close()


@pytest_asyncio.fixture
async def client(db_session):
    async def _db():
        yield db_session
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": "finance_manager", "full_name": "Test",
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           headers={"Authorization": f"Bearer {tok}"}) as c:
        yield c
    app.dependency_overrides.clear()


def _bill(bill_no, lines, *, sc="S1", name="Can Co", ccy="CAD", status=(1, 1),
          day=date(2025, 11, 29), trade="D1"):
    """lines: [(invoice_no, billed, open)]"""
    bid = uuid.uuid4()
    when = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    _exec("insert into nc_ap_bills (id, nc_pk, bill_no, trade_type, bill_status, "
          "approve_status, supplier_code, supplier_name, currency, money, bill_date, "
          "nc_ts, synced_at, created_at, updated_at) values "
          "(%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,'2026-09-01 00:00:00',now(),now(),now())",
          (bid, f"PK{bill_no}", bill_no, trade, status[0], status[1], sc, name, ccy, when))
    for n, (inv, cr, bal) in enumerate(lines):
        _exec("insert into nc_ap_bill_lines (id, bill_id, nc_pk, bill_no, row_no, bill_date, "
              "money_cr, money_bal, invoice_no, invoice_no_norm, supplier_code, supplier_name, "
              "nc_ts, synced_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
              "'2026-09-01 00:00:00',now())",
              (uuid.uuid4(), bid, f"L{bill_no}{n}", bill_no, n, when, cr, bal, inv,
               "".join(inv.upper().split()) if inv else None, sc, name))
    return bid


def _payment(bill_no, amount, *, sc="S1", ccy="CAD"):
    pid = uuid.uuid4()
    _exec("insert into nc_ap_payments (id, nc_pk, bill_no, bill_status, approve_status, "
          "currency, nc_ts, synced_at, created_at, updated_at) values "
          "(%s,%s,%s,1,1,%s,'2026-09-01 00:00:00',now(),now(),now())",
          (pid, f"PP{bill_no}", f"P{bill_no}", ccy))
    _exec("insert into nc_ap_payment_lines (id, payment_id, nc_pk, bill_no, money_de, currency, "
          "supplier_code, top_bill_type, top_bill_id, nc_ts, synced_at) values "
          "(%s,%s,%s,%s,%s,%s,%s,'F1',%s,'2026-09-01 00:00:00',now())",
          (uuid.uuid4(), pid, f"PL{bill_no}", f"P{bill_no}", amount, ccy, sc, f"PK{bill_no}"))


def _jv(jv_number, summary, *, debit=0, credit=0, partner="Can Co", account="220201",
        ccy="CAD"):
    jv = uuid.uuid4()
    _exec("insert into journal_vouchers (id, jv_number, voucher_word, voucher_date, "
          "fiscal_period, summary, status, total_debit, total_credit, total_local_debit, "
          "total_local_credit, source_subsystem, created_at, updated_at) values "
          "(%s,%s,'JV','2025-12-15','2025-12',%s,'posted',0,0,0,0,'GL',now(),now())",
          (jv, jv_number, summary))
    _exec("insert into journal_voucher_lines (id, jv_id, line_no, account_code, summary, "
          "orig_debit, orig_credit, local_debit, local_credit, currency, fx_rate, partner_name, "
          "created_at, updated_at) values (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,1,%s,now(),now())",
          (uuid.uuid4(), jv, account, summary, debit, credit, debit, credit, ccy, partner))


def test_invoice_tokens_expand_compound_numbers():
    assert invoice_tokens("9151970/1987/1992") == ["9151970", "9151987", "9151992"]
    assert invoice_tokens("9152018/005/036/023") == ["9152018", "9152005", "9152036", "9152023"]
    assert invoice_tokens("9152756/9152790/CR590-0") == ["9152756", "9152790", "CR590-0"]
    assert invoice_tokens(" #inv-69389 ") == ["INV-69389"]
    assert invoice_tokens("2020") == ["2020"] and invoice_tokens("123") == []
    assert invoice_tokens(None) == []


async def test_search_box_finds_invoice_numbers(client, db_session):
    _bill("D1", [("A-1", "10", "10")], sc="S1", name="Alpha")
    _bill("D2", [("ZX 99-7", "20", "0"), ("B-2", "5", "5")], sc="S2", name="Beta")
    r = await client.get("/finance/v1/ap-ledger-health/items",
                         params={"currency": "CAD", "q": "zx99"})
    body = r.json()
    assert body["total"] == 1, body
    row = body["items"][0]
    assert row["supplier_code"] == "S2"
    # Says which bill, and that it is already settled (paid bills count too).
    assert [(h["bill_no"], h["money_bal"]) for h in row["invoice_hits"]] == [("D2", "0.00")]
    # Vendor search unchanged, and no invoice hits reported for it.
    r = await client.get("/finance/v1/ap-ledger-health/items",
                         params={"currency": "CAD", "q": "alph"})
    assert [i["supplier_code"] for i in r.json()["items"]] == ["S1"]
    assert r.json()["items"][0]["invoice_hits"] == []
    # Too short to search invoices by: only vendors.
    r = await client.get("/finance/v1/ap-ledger-health/items",
                         params={"currency": "CAD", "q": "b-"})
    assert r.json()["total"] == 0


async def test_excluded_view_searches_invoices_by_whole_bill(client, db_session):
    _bill("X1", [("Q-100", "10", "10"), ("Q-200", "7", "7")], status=(1, 0))
    _bill("X2", [("R-300", "4", "4")], status=(1, 0), sc="S9", name="Other")
    r = await client.get("/finance/v1/ap-ledger-health/abandoned",
                         params={"currency": "CAD", "q": "q-200"})
    body = r.json()
    assert body["total"] == 1
    # The whole bill comes back, not just the matching line.
    assert body["items"][0]["bill_no"] == "X1" and body["items"][0]["money_bal"] == "17.00"


async def test_unpaid_bill_found_in_a_jv(client, db_session):
    _bill("D10", [("9151970/1987", "300.00", "300.00")])
    _bill("D11", [("777001", "50.00", "50.00")])            # paid by a payment doc
    _payment("D11", "50.00")
    _jv("JV-1", "Clear Can Co inv 9151987 by journal", debit="300.00")    # the clearing
    _jv("JV-2", "Can Co booking 9151970", credit="300.00")                # wrong direction
    _jv("JV-3", "Other co 9151970", debit="300.00", partner="Someone Else")
    _jv("JV-4", "Can Co 91519870 longer number", debit="1.00")            # not the number
    _jv("JV-5", "Can Co 777001", debit="50.00")                           # bill has a payment
    _jv("JV-6", "Can Co 9151970 expense side", debit="300.00", account="6602")

    r = await client.get("/finance/v1/ap-ledger-health/supplier",
                         params={"supplier_code": "S1", "currency": "CAD"})
    bills = {b["bill_no"]: b for b in r.json()["open_bills"]}
    m = bills["D10"]["jv_matches"]
    assert [(x["jv_number"], x["invoice_no"], x["amount_matches"]) for x in m] == \
        [("JV-1", "9151987", True)]
    assert bills["D11"]["jv_matches"] == []

    r = await client.get("/finance/v1/ap-ledger-health/bills", params={"currency": "CAD"})
    rows = {b["bill_no"]: b for b in r.json()["items"]}
    assert [x["jv_number"] for x in rows["D10"]["jv_matches"]] == ["JV-1"]
    assert rows["D11"]["payment_lines"] == 1 and rows["D11"]["jv_matches"] == []


async def test_same_invoice_in_another_currency(client, db_session):
    _bill("C1", [("9151970/1987/1992", "189421.35", "189421.35")])
    _bill("U1", [("9151987", "43261.92", "0")], ccy="USD", trade="F1-Cxx-017",
          day=date(2025, 11, 12))
    _bill("U2", [("9151992", "43261.92", "0")], ccy="USD", trade="F1-Cxx-017")
    _bill("U3", [("9151987", "1", "1")], ccy="USD", sc="S7", name="Not them")   # other supplier
    _bill("C2", [("9151970", "1", "1")])                                      # same currency

    r = await client.get("/finance/v1/ap-ledger-health/supplier",
                         params={"supplier_code": "S1", "currency": "CAD"})
    bills = {b["bill_no"]: b for b in r.json()["open_bills"]}
    tw = bills["C1"]["other_currency"]
    assert [(t["bill_no"], t["currency"], t["invoice_nos"], t["money_bal"]) for t in tw] == \
        [("U1", "USD", ["9151987"], "0.00"), ("U2", "USD", ["9151992"], "0.00")]
    assert bills["C2"]["other_currency"] == []
