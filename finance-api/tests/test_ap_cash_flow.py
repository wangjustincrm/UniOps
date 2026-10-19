"""AP cash-flow forecast — one row per NC payable, whatever the vendor master holds.

`vendors.erp_id` is not unique in production (Ecolab 0000004 exists twice, 15
supplier codes in all), and joining the forecast to it straight listed every
such bill once per copy and added it into the bucket totals once per copy.
"""
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg2

from app.services import ap_cash_flow as svc

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
             f"user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")


def _exec(sql, params=()):
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor(); cur.execute(sql, params)
    con.close()


def _vendors_table():
    # epms-api owns `vendors`; finance-api has no mirror model, so the test
    # schema has no such table. Only the columns the forecast reads.
    _exec("create table if not exists vendors (id uuid primary key, erp_id varchar(50), "
          "payment_terms varchar(50), is_active boolean, updated_at timestamptz)")


def _vendor(erp_id, terms, *, active=True, updated=datetime(2026, 9, 1, tzinfo=timezone.utc)):
    _exec("insert into vendors values (%s,%s,%s,%s,%s)",
          (uuid.uuid4(), erp_id, terms, active, updated))


def _bill(bill_no, amount, *, sc, day):
    bid = uuid.uuid4()
    when = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    _exec("insert into nc_ap_bills (id, nc_pk, bill_no, trade_type, bill_status, "
          "approve_status, supplier_code, supplier_name, currency, money, bill_date, "
          "nc_ts, synced_at, created_at, updated_at) values "
          "(%s,%s,%s,'F1-Cxx-017',1,1,%s,%s,'CAD',%s,%s,'2026-09-01 00:00:00',now(),now(),now())",
          (bid, f"PK{bill_no}", bill_no, sc, f"Supplier {sc}", amount, when))
    _exec("insert into nc_ap_bill_lines (id, bill_id, nc_pk, bill_no, bill_date, money_cr, "
          "money_bal, invoice_no, invoice_no_norm, supplier_code, supplier_name, nc_ts, "
          "synced_at) values (%s,%s,%s,%s,%s,%s,%s,'INV','INV',%s,%s,'2026-09-01 00:00:00',now())",
          (uuid.uuid4(), bid, f"L{bill_no}", bill_no, when, amount, amount, sc, f"Supplier {sc}"))


async def test_duplicate_vendor_records_do_not_repeat_a_bill(db_session):
    _vendors_table()
    # Same NC supplier created twice; the inactive copy carries different terms.
    _vendor("0000004", "net30")
    _vendor("0000004", "net90", active=False, updated=datetime(2026, 10, 1, tzinfo=timezone.utc))
    _vendor("0000005", "net30")                        # the control: one record
    day = date.today() - timedelta(days=10)
    _bill("D2001", "5974.00", sc="0000004", day=day)
    _bill("D2002", "100.00", sc="0000005", day=day)

    got = await svc.items(db_session, "CAD", limit=1000)
    assert got["total"] == 2 and got["amount"] == "6074.00"
    rows = got["items"]
    assert sorted(r["bill_no"] for r in rows) == ["D2001", "D2002"]
    ecolab = next(r for r in rows if r["bill_no"] == "D2001")
    # The active record's terms win over the more recently edited inactive one.
    assert ecolab["payment_terms"] == "net30"
    assert ecolab["due_date"] == (day + timedelta(days=30)).isoformat()

    summary = await svc.summary(db_session, "CAD")
    cad = next(c for c in summary["currencies"] if c["currency"] == "CAD")
    assert sum(b["bills"] for b in cad["buckets"]) == 2
    assert cad["total"] == "6074.00"
