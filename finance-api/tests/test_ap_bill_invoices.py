"""An NC payable carries one supplier invoice per LINE, often several.

The bill-level AP lists used to show `min(invoice_no)` beside the bill's total,
so a seven-invoice freight bill (D12026092800139647, 4,700.13) read as one
invoice of 4,700.13. Every such list now returns the invoices with their own
amounts, and those amounts add up to the row's totals.
"""
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.services.nc_ap_bill_invoices import invoices

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
             f"user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")

# (row_no, invoice_no, billed, open, purchase order, summary) — the first three
# lines of the real bill, plus the same invoice split over a second line and a
# line nobody numbered.
LINES = [
    (0, "3417820", "395.50", "395.50", None, "Shoppers PO#0008040134"),
    (10, "3418010", "452.00", "452.00", "PO-7", "Market donate Niuriss"),
    (20, "3416653", "474.65", "0.00", None, "Save on Food PO#586376"),
    (30, "3418010", "100.00", "100.00", "PO-8", "Market donate Niuriss (2)"),
    (40, None, "10.00", "10.00", None, None),
]


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


def _bill(bill_no, *, status=(1, 1), day=None, lines=LINES):
    day = day or date.today() - timedelta(days=5)
    bid = uuid.uuid4()
    when = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    total = sum(Decimal(m) for _, _, m, _, _, _ in lines)
    _exec("insert into nc_ap_bills (id, nc_pk, bill_no, trade_type, bill_status, "
          "approve_status, supplier_code, supplier_name, currency, money, bill_date, "
          "nc_ts, synced_at, created_at, updated_at) values "
          "(%s,%s,%s,'F1-Cxx-017',%s,%s,'S9','Radiant','CAD',%s,%s,"
          "'2026-09-01 00:00:00',now(),now(),now())",
          (bid, f"PK{bill_no}", bill_no, status[0], status[1], total, when))
    # Inserted out of NC's order on purpose: the breakdown follows row_no.
    for row_no, inv, cr, bal, po, memo in reversed(lines):
        _exec("insert into nc_ap_bill_lines (id, bill_id, nc_pk, bill_no, row_no, "
              "bill_date, money_cr, money_bal, invoice_no, invoice_no_norm, "
              "supplier_code, supplier_name, purchase_order, scomment, nc_ts, synced_at) "
              "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'S9','Radiant',%s,%s,"
              "'2026-09-01 00:00:00',now())",
              (uuid.uuid4(), bid, f"L{bill_no}{row_no}", bill_no, row_no, when, cr, bal,
               inv, inv.upper() if inv else None, po, memo))


def _vendor():
    _exec("create table if not exists vendors (id uuid primary key, erp_id varchar(50), "
          "payment_terms varchar(50), is_active boolean, updated_at timestamptz)")
    _exec("insert into vendors values (%s,'S9','net30',true,now())", (uuid.uuid4(),))


def _assert_breakdown(row, *, open_only):
    """The invoices, in NC's order, with their own money — and they add up."""
    got = row["invoices"]
    nos = [i["invoice_no"] for i in got]
    if open_only:
        # Lists of OPEN bills aggregate only the lines still open, so a paid
        # line's invoice is not on the row — and the row's totals agree.
        assert nos == ["3417820", "3418010", None]
    else:
        assert nos == ["3417820", "3418010", "3416653", None]
    split = next(i for i in got if i["invoice_no"] == "3418010")
    assert split["lines"] == 2
    assert Decimal(split["money_cr"]) == Decimal("552.00")
    assert Decimal(split["money_bal"]) == Decimal("552.00")
    assert split["purchase_orders"] == ["PO-7", "PO-8"]
    assert split["summary"] == "Market donate Niuriss"
    return (sum(Decimal(i["money_cr"]) for i in got),
            sum(Decimal(i["money_bal"]) for i in got))


def test_invoices_merges_split_lines_and_keeps_order():
    raw = [{"invoice_no": " A1 ", "invoice_no_norm": "A1", "purchase_order": None,
            "summary": "", "money_cr": "1.10", "money_bal": "1.10"},
           {"invoice_no": "b2", "invoice_no_norm": "B2", "purchase_order": "P",
            "summary": "x", "money_cr": "2.00", "money_bal": "0.00"},
           {"invoice_no": "a1", "invoice_no_norm": "A1", "purchase_order": "P",
            "summary": "y", "money_cr": "3.00", "money_bal": None}]
    got = invoices(raw)
    assert [i["invoice_no"] for i in got] == ["A1", "b2"]
    assert got[0] == {"invoice_no": "A1", "purchase_orders": ["P"], "summary": "y",
                      "lines": 2, "money_cr": "4.10", "money_bal": "1.10"}
    assert invoices(None) == []


async def test_cash_flow_rows_carry_every_invoice(client, db_session):
    _vendor()
    _bill("D1")
    r = await client.get("/finance/v1/ap-cash-flow/items", params={"currency": "CAD"})
    assert r.status_code == 200, r.text
    (row,) = r.json()["items"]
    _, bal = _assert_breakdown(row, open_only=True)
    assert bal == Decimal(row["open_bal"]) == Decimal("957.50")


async def test_ledger_health_bills_by_date_rows_carry_every_invoice(client, db_session):
    _bill("D1")
    r = await client.get("/finance/v1/ap-ledger-health/bills", params={"currency": "CAD"})
    assert r.status_code == 200, r.text
    (row,) = r.json()["items"]
    cr, bal = _assert_breakdown(row, open_only=True)
    assert (cr, bal) == (Decimal(row["money_cr"]), Decimal(row["money_bal"]))


async def test_ledger_health_supplier_detail_carries_every_invoice(client, db_session):
    _bill("D1")
    r = await client.get("/finance/v1/ap-ledger-health/supplier",
                         params={"supplier_code": "S9", "currency": "CAD"})
    assert r.status_code == 200, r.text
    (row,) = r.json()["open_bills"]
    cr, bal = _assert_breakdown(row, open_only=True)
    assert (cr, bal) == (Decimal(row["money_cr"]), Decimal(row["money_bal"]))


async def test_abandoned_rows_carry_every_invoice(client, db_session):
    _bill("D1", status=(1, 0))
    r = await client.get("/finance/v1/ap-ledger-health/abandoned", params={"currency": "CAD"})
    assert r.status_code == 200, r.text
    (row,) = r.json()["items"]
    cr, bal = _assert_breakdown(row, open_only=True)
    assert (cr, bal) == (Decimal(row["money_cr"]), Decimal(row["money_bal"]))
