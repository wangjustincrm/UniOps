"""Payment Batch "NC Paid": what NC's books say about each EPMS invoice on the
due list, read from the NC bill line's open balance (2026-11-11)."""
import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg2

from app.crud import payment_batch as batch_crud
from app.services import nc_invoice_paid as svc
from tests.test_ap_cash_flow import _TEST_DSN, _bill, _exec, _pa, _vendor, _vendors_table


def _one(sql, params=()):
    con = psycopg2.connect(_TEST_DSN)
    cur = con.cursor(); cur.execute(sql, params)
    row = cur.fetchone(); con.close()
    return row[0] if row else None


def _inv_id(number):
    return str(_one("select id from invoices where vendor_invoice_number=%s", (number,)))


def _settle(bill_no, invoice_no, bal="0"):
    _exec("update nc_ap_bill_lines set money_bal=%s where bill_no=%s and invoice_no=%s",
          (bal, bill_no, invoice_no))


def _nc_payment(bill_no, pay_no, *, day=date(2026, 9, 26), status=(1, 1)):
    pid = uuid.uuid4()
    when = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    _exec("insert into nc_ap_payments (id, nc_pk, bill_no, bill_status, approve_status, "
          "pay_date, nc_ts, synced_at, created_at, updated_at) values "
          "(%s,%s,%s,%s,%s,%s,'2026-09-01 00:00:00',now(),now(),now())",
          (pid, f"PK{pay_no}", pay_no, status[0], status[1], when))
    _exec("insert into nc_ap_payment_lines (id, payment_id, nc_pk, bill_no, top_bill_id, "
          "nc_ts, synced_at) values (%s,%s,%s,%s,%s,'2026-09-01 00:00:00',now())",
          (uuid.uuid4(), pid, f"PL{pay_no}{bill_no}", pay_no, f"PK{bill_no}"))


async def test_each_state_is_read_from_the_line_balance(db_session):
    _vendors_table()
    v = _vendor("0000101", "net30")
    _pa("PA-N-0001", v, ["P-1", "C-2", "H-3", "O-4", "X-5"])
    day = date.today() - timedelta(days=3)
    _bill("D9001", "100.00", sc="0000101", day=day, inv="P1",       # hyphen ignored
          more=[("O-4", "400.00")])
    _settle("D9001", "P1")
    _nc_payment("D9001", "D3PAY1")
    _bill("D9002", "200.00", sc="0000101", day=day, inv="C-2")
    _settle("D9002", "C-2")                     # zero balance, no payment
    _bill("D9003", "300.00", sc="0000101", day=day, inv="H-3")
    _settle("D9003", "H-3", "120.00")

    got = await svc.invoice_status(db_session, [_inv_id(n) for n in
                                                ("P-1", "C-2", "H-3", "O-4", "X-5")])
    st = {n: got[_inv_id(n)] for n in ("P-1", "C-2", "H-3", "O-4", "X-5")}
    assert st["P-1"]["status"] == svc.PAID
    assert st["P-1"]["payment_no"] == "D3PAY1" and st["P-1"]["paid_on"] == "2026-09-26"
    assert st["C-2"]["status"] == svc.CLOSED
    assert st["H-3"]["status"] == svc.PARTIAL and st["H-3"]["open_balance"] == "120.00"
    # Same bill as a paid invoice, and the bill has a payment: still open.
    assert st["O-4"]["status"] == svc.OPEN and st["O-4"]["bill_no"] == "D9001"
    assert st["X-5"]["status"] == svc.NOT_IN_NC and st["X-5"]["bill_no"] is None


async def test_only_approved_nc_documents_and_the_right_supplier_count(db_session):
    _vendors_table()
    v = _vendor("0000102", "net30")
    _pa("PA-N-0002", v, ["D-1", "S-2", "R-3"])
    day = date.today()
    _bill("D9101", "10.00", sc="0000102", day=day, inv="D-1")
    _settle("D9101", "D-1")
    _exec("update nc_ap_bills set approve_status=-1 where bill_no='D9101'")   # NC draft
    _bill("D9102", "20.00", sc="0000999", day=day, inv="S-2")                 # other supplier
    _settle("D9102", "S-2")
    _bill("D9103", "30.00", sc="0000102", day=day, inv="R-3")
    _settle("D9103", "R-3")
    _nc_payment("D9103", "D3VOID", status=(-1, 3))                           # voided payment

    got = await svc.invoice_status(db_session, [_inv_id(n) for n in ("D-1", "S-2", "R-3")])
    assert got[_inv_id("D-1")]["status"] == svc.NOT_IN_NC
    assert got[_inv_id("S-2")]["status"] == svc.NOT_IN_NC
    # Settled, but the only payment was voided: closed, not paid by D3VOID.
    assert got[_inv_id("R-3")]["status"] == svc.CLOSED
    assert got[_inv_id("R-3")]["payment_no"] is None


async def test_an_old_bill_reusing_the_number_is_not_a_match(db_session):
    # Hydro One PA-20261026-0005 matched a settled 2021 bill on the same number.
    _vendors_table()
    v = _vendor("0000103", "net30")
    _pa("PA-N-0003", v, ["200245176662"], dates={"200245176662": (date(2026, 10, 1),
                                                                  date(2026, 10, 31))})
    _bill("D9201", "71246.14", sc="0000103", day=date(2021, 6, 25), inv="200245176662")
    _settle("D9201", "200245176662")
    _nc_payment("D9201", "D3OLD", day=date(2021, 7, 30))
    _bill("D9202", "500.00", sc="0000103", day=date(2026, 9, 20), inv="200245176662")

    got = await svc.invoice_status(db_session, [_inv_id("200245176662")])
    r = got[_inv_id("200245176662")]
    assert r["status"] == svc.OPEN and r["bill_no"] == "D9202"


def test_rollup():
    assert svc.rollup([]) is None
    assert svc.rollup([svc.PAID, svc.CLOSED]) == svc.PAID
    assert svc.rollup([svc.CLOSED]) == svc.CLOSED
    assert svc.rollup([svc.NOT_IN_NC, svc.NOT_IN_NC]) == svc.NOT_IN_NC
    assert svc.rollup([svc.OPEN, svc.NOT_IN_NC]) == svc.OPEN
    assert svc.rollup([svc.PAID, svc.OPEN]) == svc.PARTIAL
    assert svc.rollup([svc.PAID, svc.NOT_IN_NC]) == svc.PARTIAL
    assert svc.rollup([svc.PARTIAL]) == svc.PARTIAL


async def test_due_list_carries_nc_paid(db_session):
    _vendors_table()
    v = _vendor("0000104", "net30")
    _pa("PA-N-0010", v, ["A-1", "A-2"])
    _pa("PA-N-0011", v, ["B-1"])
    day = date.today()
    _bill("D9301", "10.00", sc="0000104", day=day, inv="A-1", more=[("A-2", "5.00")])
    _settle("D9301", "A-1"); _settle("D9301", "A-2")
    _nc_payment("D9301", "D3PAY10")
    _bill("D9302", "10.00", sc="0000104", day=day, inv="B-1")

    rows = {r["doc_number"]: r for r in await batch_crud.list_due(db_session, "CAD")}
    paid, open_ = rows["PA-N-0010"], rows["PA-N-0011"]
    assert paid["nc_paid"] == svc.PAID and paid["nc_paid_count"] == 2
    assert {i["nc"]["payment_no"] for i in paid["invoices"]} == {"D3PAY10"}
    # The control: a PA NC has not paid is admitted as open, not hidden.
    assert open_["nc_paid"] == svc.OPEN and open_["nc_paid_count"] == 0
