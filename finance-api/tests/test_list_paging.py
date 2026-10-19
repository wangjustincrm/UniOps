"""Server paging + server sort for the lists that used to be loaded whole with a
cap (AR invoices 200, payment batches 200, AP cash-flow bills 1000, AP ledger
health suppliers/abandoned 500 and bills 1000, AP duplicate groups 2000,
unallocated lines 300). For each: `total` counts every matching row, page 2
continues page 1 under every sort key in both directions, NULLs sort last both
ways, and an unknown key is a 422.
"""
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.services import qbo_sync

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
             f"user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")


def _exec(sql, params=()):
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor(); cur.execute(sql, params)
    con.close()


@pytest.fixture(autouse=True)
def never_touch_quickbooks(monkeypatch):
    monkeypatch.setattr(qbo_sync, "qbo_configured", lambda: False)
    monkeypatch.setattr(qbo_sync, "launch_sync", lambda **kw: pytest.fail("QBO sync launched"))


def _h(role="finance_manager"):
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": role, "full_name": "Test",
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _db():
        yield db_session
    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    app.dependency_overrides.clear()


async def _page(c, url, params, items_key):
    r = await c.get(url, params=params, headers=_h())
    assert r.status_code == 200, (params, r.text)
    body = r.json()
    return body, body[items_key]


async def _check(c, url, base, keys, *, n, items_key="items", ident, page_size=2,
                 nullable=()):
    """Every key x both directions: total == n, the pages joined == the whole
    sorted list, and for `nullable` keys the empty values are all at the end."""
    for key, value in keys.items():
        for d in ("asc", "desc"):
            p = {**base, "sort": key, "dir": d}
            body, full = await _page(c, url, {**p, "limit": 200, "offset": 0}, items_key)
            assert body["total"] == n and len(full) == n, (key, d, body.get("total"))
            pages = []
            for off in range(0, n, page_size):
                b, rows = await _page(c, url, {**p, "limit": page_size, "offset": off}, items_key)
                assert b["total"] == n
                pages += rows
            assert [ident(r) for r in pages] == [ident(r) for r in full], (key, d)
            vals = [value(r) for r in full]
            filled = [v for v in vals if v not in (None, "")]
            assert vals[:len(filled)] == filled, (key, d, "empties not last")
            assert filled == sorted(filled, reverse=(d == "desc")), (key, d, vals)
            if key in nullable:
                assert len(filled) < len(vals), (key, "fixture has no empty value")
    r = await c.get(url, params={**base, "sort": "nope"}, headers=_h())
    assert r.status_code == 422
    r = await c.get(url, params={**base, "sort": next(iter(keys)), "dir": "up"}, headers=_h())
    assert r.status_code == 422


def _low(v):
    return v.lower() if isinstance(v, str) else v


def _num(v):
    return None if v is None else Decimal(v)


# ── GET /ar/invoices ─────────────────────────────────────────────────────────

async def test_ar_invoices_paged_and_sorted(client, db_session):
    from app.models.ar import ArInvoice
    rows = [("AR-3", "beta", "posted", "100", "30", 3), ("AR-1", "Alpha", "draft", "50", "0", 1),
            ("AR-5", "gamma", "partially_paid", "80", "10", 5), ("AR-2", "delta", "paid", "40", "40", 2),
            ("AR-4", "Epsilon", "posted", "70", "0", 4)]
    for num, cust, st, tot, paid, d in rows:
        db_session.add(ArInvoice(invoice_number=num, customer_id=uuid.uuid4(), customer_name=cust,
                                 amount=Decimal(tot), total_amount=Decimal(tot),
                                 paid_amount=Decimal(paid), invoice_date=date(2026, 6, d),
                                 due_date=date(2026, 7, 6 - d), status=st))
    await db_session.flush()

    def outstanding(r):
        return (Decimal(r["total_amount"]) - Decimal(r["paid_amount"])
                if r["status"] in ("posted", "partially_paid") else None)
    await _check(client, "/finance/v1/ar/invoices", {}, {
        "invoice_number": lambda r: r["invoice_number"],
        "customer_name": lambda r: r["customer_name"].lower(),
        "invoice_date": lambda r: r["invoice_date"],
        "due_date": lambda r: r["due_date"],
        "total_amount": lambda r: Decimal(r["total_amount"]),
        "outstanding": outstanding,
        "status": lambda r: r["status"],
    }, n=5, ident=lambda r: r["id"], nullable=("outstanding",))
    # no sort = newest invoice_date first, as before
    _, got = await _page(client, "/finance/v1/ar/invoices", {}, "items")
    assert [r["invoice_number"] for r in got] == ["AR-5", "AR-4", "AR-3", "AR-2", "AR-1"]


# ── GET /payments/batches ────────────────────────────────────────────────────

async def test_payment_batches_paged_and_sorted(client, db_session):
    from app.models.payment_batch import PaymentBatch
    for i, (st, method, total) in enumerate([("draft", "eft", "10"), ("executed", "cheque", "30"),
                                            ("cancelled", "wire", "20"), ("executed", "eft", "5"),
                                            ("draft", "bank_transfer", "50")]):
        db_session.add(PaymentBatch(batch_number=f"BP-{9 - i}", batch_date=date(2026, 7, 1 + i),
                                    status=st, currency="CAD", total=Decimal(total),
                                    payment_method=method, created_by=uuid.uuid4()))
    await db_session.flush()
    await _check(client, "/finance/v1/payments/batches", {}, {
        "batch_number": lambda r: r["batch_number"],
        "batch_date": lambda r: r["batch_date"],
        "status": lambda r: r["status"],
        "payment_method": lambda r: r["payment_method"],
        "total": lambda r: Decimal(r["total"]),
    }, n=5, ident=lambda r: r["id"])


# ── NC mirror seeding (same shape as tests/test_ap_duplicate_invoices.py) ────

def _bill(bill_no, amount, *, sc, name=None, inv="INV-1", ccy="CAD", open_=0,
          status=(1, 1), day=date(2026, 9, 1), po=None):
    bid = uuid.uuid4()
    when = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    supplier = name if name is not None else f"Supplier {sc}"
    _exec("insert into nc_ap_bills (id, nc_pk, bill_no, trade_type, bill_status, "
          "approve_status, supplier_code, supplier_name, currency, money, bill_date, "
          "nc_ts, synced_at, created_at, updated_at) values "
          "(%s,%s,%s,'F1-Cxx-017',%s,%s,%s,%s,%s,%s,%s,'2026-09-01 00:00:00',now(),now(),now())",
          (bid, f"PK{bill_no}", bill_no, status[0], status[1], sc, supplier, ccy, amount, when))
    _exec("insert into nc_ap_bill_lines (id, bill_id, nc_pk, bill_no, bill_date, money_cr, "
          "money_bal, invoice_no, invoice_no_norm, supplier_code, supplier_name, "
          "purchase_order, nc_ts, synced_at) values "
          "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'2026-09-01 00:00:00',now())",
          (uuid.uuid4(), bid, f"L{bill_no}", bill_no, when, amount, open_, inv,
           "".join(inv.upper().split()) if inv else None, sc, supplier, po))


def _vendors(*codes):
    _exec("create table if not exists vendors (id uuid primary key, erp_id varchar(50), "
          "payment_terms varchar(50), is_active boolean, updated_at timestamptz)")
    for code in codes:
        _exec("insert into vendors values (%s,%s,'net30',true,now())", (uuid.uuid4(), code))


def _seed_open_bills():
    """Five open approved bills, five suppliers (one with no name), some with
    no invoice number / PO — so every column has something to sort and empties
    to push last."""
    _bill("B05", "500.00", sc="S5", name="beta", open_="500.00", inv="X-10", po="PO-2",
          day=date(2026, 8, 5))
    _bill("B01", "100.00", sc="S1", name="Alpha", open_="60.00", inv="X-9", po=None,
          day=date(2026, 8, 1))
    _bill("B03", "300.00", sc="S3", name="", open_="300.00", inv="", po="po-1",
          day=date(2026, 8, 3))
    _bill("B02", "200.00", sc="S2", name="gamma", open_="20.00", inv="X-11", po="PO-3",
          day=date(2026, 8, 2))
    _bill("B04", "400.00", sc="S4", name="Delta", open_="400.00", inv="X-12", po=None,
          day=date(2026, 8, 4))


# ── GET /ap-cash-flow/items ──────────────────────────────────────────────────

async def test_ap_cash_flow_items_paged_and_sorted(client, db_session):
    _vendors("S1", "S2", "S3", "S4", "S5")
    _seed_open_bills()
    await _check(client, "/finance/v1/ap-cash-flow/items", {"currency": "CAD"}, {
        "bill_no": lambda r: r["bill_no"],
        "supplier": lambda r: _low(r["supplier_name"] or r["supplier_code"]),
        "invoice_no": lambda r: _low(r["invoice_no"]),
        "purchase_order": lambda r: _low(r["purchase_order"]),
        "bill_date": lambda r: r["bill_date"],
        "due_date": lambda r: r["due_date"],
        "open_bal": lambda r: Decimal(r["open_bal"]),
    }, n=5, ident=lambda r: r["bill_no"], nullable=("purchase_order",))


# ── GET /ap-ledger-health/{bills,items,abandoned} ────────────────────────────

async def test_ap_ledger_health_bills_paged_and_sorted(client, db_session):
    _seed_open_bills()
    await _check(client, "/finance/v1/ap-ledger-health/bills", {"currency": "CAD"}, {
        "bill_no": lambda r: r["bill_no"],
        "bill_date": lambda r: r["bill_date"],
        "supplier": lambda r: _low(r["supplier_name"] or r["supplier_code"]),
        "invoice_no": lambda r: _low(r["invoice_no"]),
        "purchase_order": lambda r: _low(r["purchase_order"]),
        "money_cr": lambda r: Decimal(r["money_cr"]),
        "money_bal": lambda r: Decimal(r["money_bal"]),
    }, n=5, ident=lambda r: r["bill_no"], nullable=("purchase_order",))


async def test_ap_ledger_health_suppliers_paged_sorted_and_searched(client, db_session):
    _seed_open_bills()
    url = "/finance/v1/ap-ledger-health/items"
    await _check(client, url, {"currency": "CAD"}, {
        "supplier": lambda r: _low(r["supplier_name"] or r["supplier_code"]),
        "billed": lambda r: Decimal(r["billed"]),
        "paid": lambda r: Decimal(r["paid"]),
        "subledger_open": lambda r: Decimal(r["subledger_open"]),
        "billed_minus_paid": lambda r: Decimal(r["billed_minus_paid"]),
        "gap": lambda r: Decimal(r["gap"]),
    }, n=5, ident=lambda r: r["supplier_code"])
    # vendor search moved to the server: name or code, case-insensitive
    body, rows = await _page(client, url, {"currency": "CAD", "q": "ALPH"}, "items")
    assert body["total"] == 1 and rows[0]["supplier_code"] == "S1"
    body, rows = await _page(client, url, {"currency": "CAD", "q": "s3"}, "items")
    assert body["total"] == 1 and rows[0]["supplier_code"] == "S3"


async def test_ap_ledger_health_abandoned_paged_and_sorted(client, db_session):
    for i, (name, amt, st) in enumerate([("beta", "50", (1, 0)), ("Alpha", "10", (0, 0)),
                                         ("gamma", "30", (1, 0)), ("Delta", "20", (0, 1)),
                                         ("", "40", (1, 0))]):
        _bill(f"A0{i}", amt, sc=f"S{i}", name=name, open_=amt, status=st,
              inv=f"Q-{i}" if i != 2 else "", day=date(2026, 8, 1 + i))
    url = "/finance/v1/ap-ledger-health/abandoned"
    await _check(client, url, {"currency": "CAD"}, {
        "bill_no": lambda r: r["bill_no"],
        "bill_date": lambda r: r["bill_date"],
        "supplier": lambda r: _low(r["supplier_name"] or r["supplier_code"]),
        "invoice_no": lambda r: _low(r["invoice_no"]),
        "money_cr": lambda r: Decimal(r["money_cr"]),
        "money_bal": lambda r: Decimal(r["money_bal"]),
        "status": lambda r: f'{r["bill_status"]}/{r["approve_status"]}',
    }, n=5, ident=lambda r: r["bill_no"], nullable=("invoice_no",))
    body, rows = await _page(client, url, {"currency": "CAD", "q": "gam"}, "items")
    assert body["total"] == 1 and rows[0]["bill_no"] == "A02"


# ── GET /ap-duplicate-invoices ───────────────────────────────────────────────

async def test_ap_duplicate_groups_paged_and_sorted(client, db_session):
    _exec("delete from tasks")
    # Five exact-duplicate groups (two copies each), different suppliers,
    # numbers and amounts; one group still has an open copy.
    groups = [("S1", "beta", "INV-10", "500.00", "500.00"), ("S2", "Alpha", "INV-9", "100.00", 0),
              ("S3", "gamma", "INV-11", "300.00", 0), ("S4", "Delta", "INV-12", "200.00", "200.00"),
              ("S5", "epsilon", "INV-13", "400.00", 0)]
    for sc, name, inv, amt, open_ in groups:
        _bill(f"{sc}-a", amt, sc=sc, name=name, inv=inv)
        _bill(f"{sc}-b", amt, sc=sc, name=name, inv=inv, open_=open_)
    url = "/finance/v1/ap-duplicate-invoices"
    base = {"kind": "exact", "reviewed": "all"}
    await _check(client, url, base, {
        "action": lambda f: f["status"].replace("_", " "),
        "invoice": lambda f: _low(f["invoice_no"] or f["invoice_no_norm"]),
        "supplier": lambda f: _low(f["supplier_name"]),
        "currency": lambda f: f["currency"],
        "amount": lambda f: _num(f["amount"]),
        "bills": lambda f: len(f["bill_nos"]),
        "extra": lambda f: Decimal(f["extra_amount"]),
        "open": lambda f: Decimal(f["open_exposure"]),
    }, n=5, items_key="findings", ident=lambda f: f["key"])
    # a group's bills stay together in its row, whatever the page
    _, rows = await _page(client, url, {**base, "sort": "supplier", "limit": 2, "offset": 2},
                          "findings")
    assert all(len(f["bills"]) == 2 for f in rows)


# ── GET /gl/budget-actual/unallocated-lines ──────────────────────────────────

def _seed_unplaced():
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor()
    for t in ("jv_line_dimensions", "journal_voucher_lines", "journal_vouchers",
              "budget_plan_lines", "budget_plans", "cost_centers", "departments",
              "budget_accounts", "chart_of_accounts"):
        cur.execute(f"delete from {t}")
    cur.execute("insert into chart_of_accounts (id, code, name, account_type, "
                "normal_balance, is_postable, parent_code, is_active, aux_dimensions, "
                "created_at, updated_at) values (%s,'6602','6602','expense','debit',"
                "true,null,true,'[]'::jsonb,now(),now())", (uuid.uuid4(),))
    dept = uuid.uuid4()
    cur.execute("insert into departments (id, code, name, is_active, created_at, updated_at) "
                "values (%s,'0106','Engineering',true,now(),now())", (dept,))
    cc = uuid.uuid4()
    cur.execute("insert into cost_centers (id, code, name, is_active, department_id, "
                "created_at, updated_at) values (%s,'GA-0106','GA-0106',true,%s,now(),now())",
                (cc, dept))
    ba = uuid.uuid4()
    cur.execute("insert into budget_accounts (id, code, name, is_active, created_at, "
                "updated_at) values (%s,'CRM00201','Training',true,now(),now())", (ba,))
    # Five unplaced lines: three with a cost centre and a text naming nothing,
    # two with no cost centre at all (one with no dimension text either).
    for n, (amt, has_cc, io_text, summ, d) in enumerate([
            (11, True, "NOSUCH-B", "beta", dept), (7, False, "CRM00201", "Alpha", None),
            (30, True, "NOSUCH-A", "gamma", dept), (5, False, None, "delta", dept),
            (19, True, "NOSUCH-C", "Epsilon", None)]):
        jv = uuid.uuid4()
        cur.execute(
            "insert into journal_vouchers (id, jv_number, voucher_word, voucher_date, "
            "fiscal_period, summary, status, total_debit, total_credit, total_local_debit, "
            "total_local_credit, created_at, updated_at) values "
            "(%s,%s,'JV','2026-08-15','2026-08','x','posted',0,0,0,0,now(),now())",
            (jv, f"JV-2026-{9 - n}"))
        lid = uuid.uuid4()
        cur.execute(
            "insert into journal_voucher_lines (id, jv_id, line_no, account_code, summary, "
            "orig_debit, orig_credit, local_debit, local_credit, currency, fx_rate, "
            "cost_center_id, department_id, income_expense_item_id, created_at, updated_at) "
            "values (%s,%s,1,'6602',%s,%s,0,%s,0,'CAD',1,%s,%s,%s,now(),now())",
            (lid, jv, summ, amt, amt, cc if has_cc else None, d,
             ba if io_text == "CRM00201" else None))
        if io_text is not None:
            cur.execute("insert into jv_line_dimensions (id, jv_line_id, dim_code, value_id, "
                        "value_text, created_at, updated_at) "
                        "values (%s,%s,'income_expense_item',%s,%s,now(),now())",
                        (uuid.uuid4(), lid, ba if io_text == "CRM00201" else None, io_text))
    con.close()


def _why(r):
    if r["missing_cost_centre"] and r["missing_budget_account"]:
        return "No cost centre, no budget account"
    if r["missing_cost_centre"]:
        return "No cost centre"
    if r["missing_budget_account"]:
        return "No budget account"
    return "Excluded by policy"


async def test_unallocated_lines_paged_and_sorted(client, db_session):
    _seed_unplaced()
    url = "/finance/v1/gl/budget-actual/unallocated-lines"
    base = {"fiscal_year": 2026, "month_from": 1, "month_to": 9, "bucket": "__unplaced__"}
    await _check(client, url, base, {
        "voucher": lambda r: r["jv_number"],
        "account": lambda r: r["account_code"],
        "dept": lambda r: r["department_code"],
        "nc_ie": lambda r: r["nc_income_expense"],
        "why": _why,
        "debit": lambda r: Decimal(r["debit"]),
        "summary": lambda r: _low(r["summary"]),
    }, n=5, items_key="rows", ident=lambda r: r["jv_id"], nullable=("dept", "nc_ie"))
    body, _ = await _page(client, url, base, "rows")
    # the footer total covers every matching line, not the page
    assert body["debit_total"] == "72.00"
