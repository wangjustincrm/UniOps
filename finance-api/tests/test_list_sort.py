"""Click-to-sort on the finance list endpoints (shared `sort` + `dir` contract,
app/core/list_sort.py). For every paged list the UI sorts through the API:
asc / desc order, NULLs last in BOTH directions, unknown key -> 422, no sort
-> the endpoint's previous default order, and page 2 continuing page 1's order.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.services import qbo_sync


@pytest.fixture(autouse=True)
def never_touch_quickbooks(monkeypatch):
    """Nothing here calls /qbo/sync; this makes sure nothing ever can."""
    monkeypatch.setattr(qbo_sync, "qbo_configured", lambda: False)
    monkeypatch.setattr(qbo_sync, "launch_sync", lambda **kw: pytest.fail("QBO sync launched"))


def _h():
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": "finance_manager",
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


async def _get(c, url, params, items_key="items"):
    r = await c.get(url, params=params, headers=_h())
    assert r.status_code == 200, r.text
    return r.json()[items_key]


def _same(got, want, tail):
    """Exact order, except the last `tail` rows (the empties, whose order
    among themselves is only the id tiebreak) compared as a set."""
    if tail:
        return got[:-tail] == want[:-tail] and set(got[-tail:]) == set(want[-tail:])
    return got == want


async def _assert_contract(c, url, base, key, asc, desc, *, field="id",
                           items_key="items", page=None, tail=0):
    """`asc` / `desc` are the expected `field` values in order, with the NULL
    rows at the end of BOTH (`tail` = how many). `page` builds paging params
    (n_on_page, page_index) for the page-2-continues-page-1 check."""
    got = [r[field] for r in await _get(c, url, {**base, "sort": key, "dir": "asc"}, items_key)]
    assert _same(got, asc, tail), f"{key} asc: {got}"
    got = [r[field] for r in await _get(c, url, {**base, "sort": key, "dir": "desc"}, items_key)]
    assert _same(got, desc, tail), f"{key} desc: {got}"
    if page:
        p1 = await _get(c, url, {**base, "sort": key, "dir": "asc", **page(2, 0)}, items_key)
        p2 = await _get(c, url, {**base, "sort": key, "dir": "asc", **page(2, 1)}, items_key)
        full = [r[field] for r in await _get(c, url, {**base, "sort": key, "dir": "asc"},
                                             items_key)]
        assert [r[field] for r in p1 + p2] == full[:4], f"{key} paging"


# ── GET /ap/invoices ─────────────────────────────────────────────────────────

async def _ap(db, n, *, vendor, due, status="posted", total="100.00", paid="0.00",
              inv_date=date(2026, 6, 1), po=None):
    from app.models.ap_invoice import ApInvoice
    inv = ApInvoice(ap_invoice_number=f"AP-{n}", source="epms", source_invoice_id=uuid.uuid4(),
                    vendor_name=vendor, vendor_invoice_number=f"V-{n}",
                    amount=Decimal(total), total_amount=Decimal(total), paid_amount=Decimal(paid),
                    invoice_date=inv_date, due_date=due, status=status, po_number=po)
    db.add(inv)
    await db.flush()
    return inv


async def test_ap_invoices_sort(client, db_session):
    a = await _ap(db_session, 1, vendor="beta", due=date(2026, 7, 2), total="100.00",
                  paid="30.00", inv_date=date(2026, 6, 3))
    b = await _ap(db_session, 2, vendor="Alpha", due=date(2026, 7, 1), total="50.00",
                  inv_date=date(2026, 6, 1))
    c_ = await _ap(db_session, 3, vendor=None, due=None, status="paid", total="80.00",
                   paid="80.00", inv_date=date(2026, 6, 2))
    d = await _ap(db_session, 4, vendor="gamma", due=date(2026, 7, 3), total="10.00",
                  inv_date=date(2026, 5, 1))
    A, B, C, D = (str(x.id) for x in (a, b, c_, d))
    url = "/finance/v1/ap/invoices"
    page = lambda n, i: {"limit": n, "offset": n * i}   # noqa: E731

    # case-insensitive name: Alpha, beta, gamma; NULL vendor last both ways
    await _assert_contract(client, url, {}, "vendor_name", [B, A, D, C], [D, A, B, C], page=page)
    await _assert_contract(client, url, {}, "due_date", [B, A, D, C], [D, A, B, C])
    # outstanding shows only for open invoices: A=70, B=50, D=10, C (paid) = empty
    await _assert_contract(client, url, {}, "outstanding", [D, B, A, C], [A, B, D, C])
    await _assert_contract(client, url, {}, "total_amount", [D, B, C, A], [A, C, B, D])

    # no sort = the old default, newest invoice_date first
    assert [r["id"] for r in await _get(client, url, {})] == [A, C, B, D]
    r = await client.get(url, params={"sort": "vendor_id"}, headers=_h())
    assert r.status_code == 422
    r = await client.get(url, params={"sort": "vendor_name", "dir": "sideways"}, headers=_h())
    assert r.status_code == 422


# ── GET /ap-recon/items ──────────────────────────────────────────────────────

async def test_ap_recon_items_sort(client, db_session):
    from sqlalchemy import text
    # epms-api owns `vendors`; the finance test schema has no such table (same
    # stand-in as tests/test_ap_cash_flow.py), only the column the recon joins.
    await db_session.execute(text(
        "create table if not exists vendors (id uuid primary key, erp_id varchar(50))"))
    # Posted invoices with no NC mirror at all -> all in missing_in_nc.
    a = await _ap(db_session, 1, vendor="beta", due=date(2026, 7, 2), total="100.00", po="PO-10")
    b = await _ap(db_session, 2, vendor="Alpha", due=date(2026, 7, 1), total="50.00", po="PO-9")
    c_ = await _ap(db_session, 3, vendor=None, due=None, total="80.00", po=None)
    d = await _ap(db_session, 4, vendor="gamma", due=date(2026, 7, 3), total="10.00", po="po-11")
    A, B, C, D = (str(x.id) for x in (a, b, c_, d))
    url = "/finance/v1/ap-recon/items"
    base = {"category": "missing_in_nc"}
    page = lambda n, i: {"limit": n, "offset": n * i}   # noqa: E731

    await _assert_contract(client, url, base, "vendor_name", [B, A, D, C], [D, A, B, C], page=page)
    await _assert_contract(client, url, base, "due_date", [B, A, D, C], [D, A, B, C])
    # PO text sorts case-insensitively; empty PO last both ways
    await _assert_contract(client, url, base, "po_number", [A, D, B, C], [B, D, A, C])

    # no sort = by money, largest first (the old order)
    assert [r["id"] for r in await _get(client, url, base)] == [A, C, B, D]
    r = await client.get(url, params={**base, "sort": "drop table"}, headers=_h())
    assert r.status_code == 422


# ── GET /gl/jv-validation/lines ──────────────────────────────────────────────

async def test_jv_validation_lines_sort(client, db_session):
    from app.services import jv_validation as svc
    from tests.test_jv_validation import _seed
    _seed()
    url = "/finance/v1/gl/jv-validation/lines"
    base = {"period_from": "2026-01", "period_to": "2026-12"}

    def key(r):
        return f'{r["line_no"]}:{r["rule"]}'

    async def rows(**p):
        return await _get(client, url, {**base, **p}, "rows")

    # Cost center: lines without one sink to the bottom in BOTH directions.
    for d in ("asc", "desc"):
        got = [r["cost_center_code"] for r in await rows(sort="cost_center", dir=d)]
        filled = [v for v in got if v is not None]
        assert filled == sorted(filled, reverse=(d == "desc")), d
        assert got[len(filled):] == [None] * (len(got) - len(filled)), d
        assert len(filled) and len(got) > len(filled)

    # Debit numeric, both ways.
    for d in ("asc", "desc"):
        got = [Decimal(r["debit"]) for r in await rows(sort="debit", dir=d)]
        assert got == sorted(got, reverse=(d == "desc")), d

    # Finding sorts the label the page prints, not the rule code.
    for d in ("asc", "desc"):
        got = [r["label"] for r in await rows(sort="finding", dir=d)]
        assert got == sorted(got, reverse=(d == "desc")), d
        assert len(set(got)) > 2
    # ...and that is a different order from the rule codes.
    codes = [r["rule"] for r in await rows(sort="finding", dir="asc")]
    assert codes != sorted(codes)
    assert set(svc.ROW_SORT) >= {"finding", "debit", "cost_center"}

    # Paging continues the same order.
    full = [key(r) for r in await rows(sort="account_code", dir="desc")]
    p1 = [key(r) for r in await rows(sort="account_code", dir="desc", limit=3, offset=0)]
    p2 = [key(r) for r in await rows(sort="account_code", dir="desc", limit=3, offset=3)]
    assert p1 + p2 == full[:6]

    # No sort = the old order (period, voucher, line, rule).
    got = await rows()
    assert [(r["fiscal_period"], r["jv_number"], r["line_no"], r["rule"]) for r in got] == \
        sorted((r["fiscal_period"], r["jv_number"], r["line_no"], r["rule"]) for r in got)

    r = await client.get(url, params={**base, "sort": "line_id; drop"}, headers=_h())
    assert r.status_code == 422


# ── GET /payments ────────────────────────────────────────────────────────────

async def test_payments_sort(client, db_session):
    from app.models.bank import BankAccount
    from app.models.mirrors import ExpenseClaim
    from app.models.payment import PaymentRecord

    bank_b = BankAccount(name="RBC USD", bank_name="RBC", account_masked="0001",
                         currency="USD", kind="bank")
    bank_a = BankAccount(name="Operating CAD", bank_name="RBC", account_masked="0128",
                         currency="CAD", kind="bank")
    claim = ExpenseClaim(claim_number="EXP-1", claim_type="EXP", status="paid",
                         employee_id=uuid.uuid4(), employee_name="Carol Claimant",
                         currency="CAD", total_amount=Decimal("5"), tax_amount=Decimal("0"),
                         net_amount=Decimal("5"))
    db_session.add_all([bank_a, bank_b, claim])
    await db_session.flush()

    def rec(n, *, vendor, kind="pa", doc_id=None, credit="0.00", bank=None, day=1, batch=None):
        return PaymentRecord(doc_kind=kind, doc_id=doc_id or uuid.uuid4(), doc_number=f"DOC-{n}",
                             vendor_name=vendor, payment_date=date(2026, 7, day),
                             payment_method="eft", amount=Decimal(10 * n),
                             credit_applied=Decimal(credit), currency="CAD",
                             recorded_by=uuid.uuid4(), status="completed",
                             bank_account_id=bank, batch_id=batch)
    a = rec(1, vendor="beta", credit="5.00", bank=bank_b.id, day=4)
    b = rec(2, vendor="Alpha", credit="2.00", bank=bank_a.id, day=2)
    # claim payment: payee is the claimant, not vendor_name
    c_ = rec(3, vendor=None, kind="expense_claim", doc_id=claim.id, day=3, batch=uuid.uuid4())
    d = rec(4, vendor=None, day=1)                     # no payee, no credit, no bank
    db_session.add_all([a, b, c_, d])
    await db_session.flush()
    A, B, C, D = (str(x.id) for x in (a, b, c_, d))
    url = "/finance/v1/payments"
    page = lambda n, i: {"page_size": n, "page": i + 1}   # noqa: E731

    await _assert_contract(client, url, {}, "payee", [B, A, C, D], [C, A, B, D], page=page)
    # credit shows a dash when nothing was netted -> empty, last both ways
    await _assert_contract(client, url, {}, "credit_applied", [B, A, C, D], [A, B, C, D], tail=2)
    # bank account by its label ("Operating CAD · …0128" < "RBC USD · …0001")
    await _assert_contract(client, url, {}, "bank_account", [B, A, C, D], [A, B, C, D], tail=2)
    await _assert_contract(client, url, {}, "amount", [A, B, C, D], [D, C, B, A])

    # no sort = newest payment_date first (the old order)
    assert [r["id"] for r in await _get(client, url, {})] == [A, C, B, D]
    r = await client.get(url, params={"sort": "vendor_id"}, headers=_h())
    assert r.status_code == 422


# ── GET /qbo/{entity} ────────────────────────────────────────────────────────

async def test_qbo_browse_sort(client, db_session):
    from app.models.qbo import QboBill
    db_session.add_all([
        QboBill(qbo_id="9", txn_date="2026-01-02", counterparty_name="beta",
                total_amt=Decimal("5"), raw={}),
        QboBill(qbo_id="10", txn_date="2026-01-03", counterparty_name="Alpha",
                total_amt=None, raw={}),
        QboBill(qbo_id="11", txn_date="2026-01-01", counterparty_name=None,
                total_amt=Decimal("7"), raw={}),
        QboBill(qbo_id="12", txn_date=None, counterparty_name="gamma",
                total_amt=Decimal("1"), raw={}),
    ])
    await db_session.flush()
    url = "/finance/v1/qbo/bills"
    page = lambda n, i: {"page_size": n, "page": i + 1}   # noqa: E731

    # numeric-string ids sort 9 < 10 < 11
    await _assert_contract(client, url, {}, "qbo_id", ["9", "10", "11", "12"],
                           ["12", "11", "10", "9"], field="qbo_id", page=page)
    await _assert_contract(client, url, {}, "counterparty_name", ["10", "9", "12", "11"],
                           ["12", "9", "10", "11"], field="qbo_id")
    await _assert_contract(client, url, {}, "total_amt", ["12", "9", "11", "10"],
                           ["11", "9", "12", "10"], field="qbo_id")

    # no sort = the old txn_date desc (Postgres DESC: NULL first, as before)
    assert [r["qbo_id"] for r in await _get(client, url, {})] == ["12", "10", "9", "11"]
    for bad in ("raw", "nope"):
        r = await client.get(url, params={"sort": bad}, headers=_h())
        assert r.status_code == 422, bad


# ── GET /journal-vouchers (already server-sorted; now NULLs-last + 2 keys) ───

async def test_journal_voucher_sort_nulls_last_and_new_keys(client, db_session):
    from app.models.journal_voucher import JournalVoucher

    def jv(n, summary, doc, prepared):
        return JournalVoucher(jv_number=f"JV-{n}", voucher_word="JV",
                              voucher_date=date(2026, 6, n), fiscal_period="2026-06",
                              summary=summary, status="posted", total_debit=Decimal(0),
                              total_credit=Decimal(0), total_local_debit=Decimal(0),
                              total_local_credit=Decimal(0), source_doc_number=doc,
                              nc_prepared_name=prepared, nc_source_pk=f"NCPK{n}")
    a, b, c_, d = (jv(1, "beta", "AP-2", "zoe"), jv(2, "Alpha", "ap-1", None),
                   jv(3, None, None, "Adam"), jv(4, "gamma", "AP-3", "mia"))
    db_session.add_all([a, b, c_, d])
    await db_session.flush()
    A, B, C, D = (str(x.id) for x in (a, b, c_, d))
    url = "/finance/v1/journal-vouchers"
    page = lambda n, i: {"limit": n, "offset": n * i}   # noqa: E731

    await _assert_contract(client, url, {}, "summary", [B, A, D, C], [D, A, B, C], page=page)
    await _assert_contract(client, url, {}, "source_doc_number", [B, A, D, C], [D, A, B, C])
    await _assert_contract(client, url, {}, "nc_prepared_name", [C, D, A, B], [A, D, C, B])
    # default unchanged: voucher_date desc
    assert [r["id"] for r in await _get(client, url, {})] == [D, C, B, A]
    r = await client.get(url, params={"sort": "summary", "dir": "up"}, headers=_h())
    assert r.status_code == 422
