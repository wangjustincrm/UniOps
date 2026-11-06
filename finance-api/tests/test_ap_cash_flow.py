"""AP cash-flow forecast — one row per NC payable, whatever the vendor master holds.

Only lines whose invoice is on an APPROVED EPMS PA count, and a reversal pair
(same supplier, currency, invoice number, open balances netting to zero) never
does (finance, 2026-11-02).

`vendors.erp_id` is not unique in production (Ecolab 0000004 exists twice, 15
supplier codes in all), and joining the forecast to it straight listed every
such bill once per copy and added it into the bucket totals once per copy.
"""
import json
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
    vid = uuid.uuid4()
    _exec("insert into vendors values (%s,%s,%s,%s,%s)",
          (vid, erp_id, terms, active, updated))
    return vid


def _pa(pa_number, vendor_id, invoice_numbers, *, status="approved"):
    """An EPMS PA over one EPMS invoice per number, all for `vendor_id`."""
    ids = []
    for n in invoice_numbers:
        iid = uuid.uuid4()
        _exec("insert into invoices (id, internal_ref, vendor_invoice_number, vendor_id, "
              "vendor_name, amount, tax_amount, total_amount, currency, invoice_date, "
              "due_date, status, created_at, updated_at) values "
              "(%s,%s,%s,%s,'V',0,0,0,'CAD',current_date,current_date,'matched',now(),now())",
              (iid, f"INV-{iid.hex[:8]}", n, vendor_id))
        ids.append(str(iid))
    _exec("insert into payment_applications (id, pa_number, title, pa_type, status, source, "
          "vendor_id, vendor_name, invoice_ids, payment_amount, currency, created_by, "
          "created_at, updated_at) values "
          "(%s,%s,'t','regular',%s,'po',%s,'V',%s::jsonb,0,'CAD',%s,now(),now())",
          (uuid.uuid4(), pa_number, status, vendor_id, json.dumps(ids), uuid.uuid4()))


def _bill(bill_no, amount, *, sc, day, inv="INV"):
    bid = uuid.uuid4()
    when = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    _exec("insert into nc_ap_bills (id, nc_pk, bill_no, trade_type, bill_status, "
          "approve_status, supplier_code, supplier_name, currency, money, bill_date, "
          "nc_ts, synced_at, created_at, updated_at) values "
          "(%s,%s,%s,'F1-Cxx-017',1,1,%s,%s,'CAD',%s,%s,'2026-09-01 00:00:00',now(),now(),now())",
          (bid, f"PK{bill_no}", bill_no, sc, f"Supplier {sc}", amount, when))
    _exec("insert into nc_ap_bill_lines (id, bill_id, nc_pk, bill_no, bill_date, money_cr, "
          "money_bal, invoice_no, invoice_no_norm, supplier_code, supplier_name, nc_ts, "
          "synced_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'2026-09-01 00:00:00',now())",
          (uuid.uuid4(), bid, f"L{bill_no}", bill_no, when, amount, amount, inv,
           inv.upper().replace(" ", ""), sc, f"Supplier {sc}"))


async def test_duplicate_vendor_records_do_not_repeat_a_bill(db_session):
    _vendors_table()
    # Same NC supplier created twice; the inactive copy carries different terms.
    v4 = _vendor("0000004", "net30")
    _vendor("0000004", "net90", active=False, updated=datetime(2026, 10, 1, tzinfo=timezone.utc))
    v5 = _vendor("0000005", "net30")                   # the control: one record
    _pa("PA-T-0004", v4, ["INV"])
    _pa("PA-T-0005", v5, ["INV"])
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


async def test_only_bills_on_an_approved_pa_count(db_session):
    _vendors_table()
    v = _vendor("0000007", "net30")
    day = date.today() - timedelta(days=5)
    _bill("D3001", "100.00", sc="0000007", day=day, inv="A-1")    # approved PA
    _bill("D3002", "200.00", sc="0000007", day=day, inv="B-2")    # paid already
    _bill("D3003", "400.00", sc="0000007", day=day, inv="C-3")    # PA still in review
    _bill("D3004", "800.00", sc="0000007", day=day, inv="D-4")    # no PA at all
    _pa("PA-T-0101", v, ["A1"])            # hyphens ignored, as on the AP list
    _pa("PA-T-0102", v, ["B-2"], status="processed")
    _pa("PA-T-0103", v, ["C-3"], status="in_review")

    got = await svc.items(db_session, "CAD", limit=1000)
    assert [r["bill_no"] for r in got["items"]] == ["D3001"]
    assert got["items"][0]["pa_numbers"] == ["PA-T-0101"]
    cad = next(c for c in (await svc.summary(db_session, "CAD"))["currencies"]
               if c["currency"] == "CAD")
    # The approved one is admitted too — the denial above is not "nothing counts".
    assert cad["total"] == "100.00"


async def test_reversal_pair_nets_out(db_session):
    _vendors_table()
    v = _vendor("0000963", "net30")
    _bill("D4001", "2821.16", sc="0000963", day=date.today() - timedelta(days=60), inv="29363598")
    _bill("D4002", "-2821.16", sc="0000963", day=date.today() - timedelta(days=20), inv="29363598")
    _bill("D4003", "50.00", sc="0000963", day=date.today(), inv="X-9")
    _pa("PA-T-0201", v, ["29363598", "X-9"])

    got = await svc.items(db_session, "CAD", limit=1000)
    assert [r["bill_no"] for r in got["items"]] == ["D4003"]


async def test_search_by_bill_invoice_supplier_or_pa(db_session):
    _vendors_table()
    v = _vendor("0000008", "net30")
    w = _vendor("0000009", "net30")
    day = date.today()
    _bill("D5001", "10.00", sc="0000008", day=day, inv="INV-777")
    _bill("D5002", "20.00", sc="0000009", day=day, inv="Q_1")
    _pa("PA-T-0301", v, ["INV-777"])
    _pa("PA-T-0302", w, ["Q_1"])

    async def hits(q):
        return [r["bill_no"] for r in (await svc.items(db_session, "CAD", q=q))["items"]]
    assert await hits("d5001") == ["D5001"]
    assert await hits("inv-777") == ["D5001"]
    assert await hits("Supplier 0000009") == ["D5002"]
    assert await hits("PA-T-0302") == ["D5002"]
    # LIKE wildcards are literal: "_" must not match any one character.
    assert await hits("Q_1") == ["D5002"]
    assert await hits("V_7") == []
    assert sorted(await hits("")) == ["D5001", "D5002"]


def _prepayment(pa_number, vendor_id, amount, *, status="approved", approved=None,
                currency="CAD", invoice_ids=()):
    _exec("insert into payment_applications (id, pa_number, title, pa_type, status, source, "
          "vendor_id, vendor_name, po_number, invoice_ids, payment_amount, currency, "
          "approved_at, created_by, created_at, updated_at) values "
          "(%s,%s,'t','prepayment',%s,'po',%s,'Prepaid Co','PO-T-1',%s::jsonb,%s,%s,%s,%s,"
          "now(),now())",
          (uuid.uuid4(), pa_number, status, vendor_id, json.dumps(list(invoice_ids)), amount,
           currency, approved, uuid.uuid4()))


def _claim(number, amount, *, claim_type="EXP", status="approved", approved=None,
           currency="CAD"):
    _exec("insert into expense_claims (id, claim_number, claim_type, status, employee_name, "
          "currency, total_amount, tax_amount, net_amount, approved_at, created_at, updated_at) "
          "values (%s,%s,%s,%s,'Pat Lee',%s,%s,0,%s,%s,now(),now())",
          (uuid.uuid4(), number, claim_type, status, currency, amount, amount, approved))


def _at(day):
    return datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)


async def test_approved_prepayment_counts_without_an_nc_bill(db_session):
    _vendors_table()
    v = _vendor("0000010", "net30")
    approved = date.today() - timedelta(days=3)
    _prepayment("PA-T-0401", v, "230.00", approved=_at(approved))
    _prepayment("PA-T-0402", v, "999.00", status="in_review")
    _prepayment("PA-T-0403", v, "999.00", status="processed", approved=_at(approved))

    got = await svc.items(db_session, "CAD", limit=1000)
    assert got["total"] == 1 and got["amount"] == "230.00"
    r = got["items"][0]
    assert (r["doc_kind"], r["doc_key"], r["bill_no"]) == ("prepayment", "PA-T-0401", None)
    assert r["pa_numbers"] == ["PA-T-0401"]
    assert r["supplier_code"] == "0000010" and r["supplier_name"] == "Prepaid Co"
    assert r["invoices"][0]["purchase_orders"] == ["PO-T-1"]
    # Due on approval, whatever the vendor's own term says.
    assert (r["payment_terms"], r["term_days"]) == ("prepayment", 0)
    assert r["due_date"] == approved.isoformat() and r["bucket"] == "overdue_now"


async def test_prepayment_already_on_an_nc_line_is_not_counted_twice(db_session):
    _vendors_table()
    v = _vendor("0000011", "net30")
    _bill("D6001", "500.00", sc="0000011", day=date.today(), inv="P-1")
    iid = uuid.uuid4()
    _exec("insert into invoices (id, internal_ref, vendor_invoice_number, vendor_id, "
          "vendor_name, amount, tax_amount, total_amount, currency, invoice_date, "
          "due_date, status, created_at, updated_at) values "
          "(%s,%s,'P-1',%s,'V',0,0,0,'CAD',current_date,current_date,'matched',now(),now())",
          (iid, f"INV-{iid.hex[:8]}", v))
    _prepayment("PA-T-0411", v, "500.00", approved=_at(date.today()), invoice_ids=[str(iid)])

    got = await svc.items(db_session, "CAD", limit=1000)
    assert [r["doc_key"] for r in got["items"]] == ["D6001"]
    assert got["amount"] == "500.00"


async def test_approved_claim_counts_and_travel_application_does_not(db_session):
    _vendors_table()
    approved = date.today() - timedelta(days=2)
    _claim("EXP-T-0001", "130.00", approved=_at(approved))
    _claim("MIL-T-0001", "377.30", claim_type="MIL", approved=_at(approved))
    _claim("TRA-T-0001", "50.00", claim_type="TRA", approved=_at(approved))
    _claim("EXP-T-0002", "999.00", status="submitted")
    _claim("EXP-T-0003", "999.00", status="paid", approved=_at(approved))

    got = await svc.items(db_session, "CAD", limit=1000)
    assert sorted(r["doc_key"] for r in got["items"]) == ["EXP-T-0001", "MIL-T-0001"]
    assert got["amount"] == "507.30"
    r = next(x for x in got["items"] if x["doc_key"] == "EXP-T-0001")
    assert r["doc_kind"] == "claim" and r["bill_no"] is None
    assert r["supplier_name"] == "Pat Lee" and r["supplier_code"] is None
    assert r["pa_numbers"] == ["EXP-T-0001"] and r["invoices"] == []
    assert r["due_date"] == approved.isoformat()


async def test_all_three_kinds_add_up_and_stay_off_the_missing_terms_list(db_session):
    _vendors_table()
    v = _vendor("0000012", "net30")
    w = _vendor("0000013", None)                   # no term at all: undated
    today = date.today()
    _bill("D7001", "100.00", sc="0000012", day=today, inv="M-1")
    _bill("D7002", "40.00", sc="0000013", day=today, inv="M-2")
    _pa("PA-T-0501", v, ["M-1"])
    _pa("PA-T-0502", w, ["M-2"])
    _prepayment("PA-T-0503", w, "20.00", approved=_at(today))
    _prepayment("PA-T-0504", v, "7.00", approved=_at(today), currency="USD")
    _claim("EXP-T-0101", "3.00", approved=_at(today))

    cad = next(c for c in (await svc.summary(db_session, "CAD"))["currencies"]
               if c["currency"] == "CAD")
    assert cad["total"] == "163.00" and cad["undated"] == "40.00"
    usd = next(c for c in (await svc.summary(db_session, "USD"))["currencies"]
               if c["currency"] == "USD")
    assert usd["total"] == "7.00"

    # Only the NC bill is on the work list, though its supplier also has a prepayment.
    missing = await svc.missing_terms(db_session, "CAD")
    assert [(m["supplier_code"], m["bills"], m["amount"]) for m in missing["items"]] \
        == [("0000013", 1, "40.00")]


async def test_search_and_sort_reach_prepayments_and_claims(db_session):
    _vendors_table()
    v = _vendor("0000014", "net30")
    day = date.today()
    _bill("D8001", "10.00", sc="0000014", day=day, inv="S-1")
    _pa("PA-T-0601", v, ["S-1"])
    _prepayment("PA-T-0602", v, "20.00", approved=_at(day))
    _prepayment("PA-T-0603", v, "20.00", approved=_at(day))
    _claim("EXP-T-0201", "30.00", approved=_at(day))

    async def hits(q):
        return [r["doc_key"] for r in (await svc.items(db_session, "CAD", q=q))["items"]]
    assert await hits("PA-T-0602") == ["PA-T-0602"]
    assert await hits("EXP-T-0201") == ["EXP-T-0201"]
    assert await hits("pat lee") == ["EXP-T-0201"]
    assert await hits("Prepaid Co") == ["PA-T-0602", "PA-T-0603"]

    # Rows with no bill number share a sort value; the tiebreak keeps paging whole.
    from app.core.list_sort import order_sql
    order = order_sql("bill_no", "asc", svc.ITEM_SORT, tiebreak=svc.ITEM_TIEBREAK)
    pages = [(await svc.items(db_session, "CAD", limit=1, offset=i, order=order))["items"][0]
             ["doc_key"] for i in range(4)]
    assert sorted(pages) == ["D8001", "EXP-T-0201", "PA-T-0602", "PA-T-0603"]
