"""Vendor + invoice number uniqueness on NC payables — rules, verdicts, task, wiring.

The rules are tested pure first (no DB): every kind gets the case that MUST
fire and the look-alike that must NOT, because the look-alikes are most of
production — a reversal, a same-day split, a draft credit note. A check that
flags all 213 same-number pairs and one that flags none both pass a
"finds the duplicate" test; only the pair separates them.
"""
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg2
import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.services import ap_duplicate_invoice_tasks as tasks
from app.services import ap_duplicate_invoices as dup

TODAY = date(2026, 9, 25)


def E(bill_no, amount, *, sc="S1", inv="INV-1", ccy="CAD", open_=0, eff=True,
      approve=None, d=date(2026, 9, 1), name=None, dismissed=False):
    return dup.Entry(
        bill_no=bill_no, nc_pk=f"PK{bill_no}", effective=eff,
        bill_status=1 if eff else -1,
        approve_status=1 if eff else (approve if approve is not None else -1),
        trade_type="F1-Cxx-017", supplier_code=sc, supplier_name=name or f"Supplier {sc}",
        currency=ccy, invoice_no=inv, invoice_no_norm="".join(inv.upper().split()),
        bill_date=d, amount=Decimal(str(amount)), open=Decimal(str(open_)),
        dismissed=dismissed)


def kinds(found):
    return sorted(f.kind for f in found)


# ── exact ────────────────────────────────────────────────────────────────────

def test_same_invoice_same_amount_twice_is_exact():
    """Jane Media 1497: 50,722.88 on two approved payables, both paid."""
    found = dup.detect([E("B1", "50722.88"), E("B2", "50722.88")], today=TODAY)
    assert kinds(found) == [dup.EXACT]
    f = found[0]
    assert f.extra_copies == 1
    assert f.extra_amount == Decimal("50722.88")
    assert f.open_exposure == 0
    assert f.status == "recover"
    assert f.bill_nos == ["B1", "B2"]


def test_single_bill_is_not_a_finding():
    assert dup.detect([E("B1", "100")], today=TODAY) == []


def test_bill_and_its_reversal_is_not_a_duplicate():
    assert dup.detect([E("B1", "567.43"), E("B2", "-567.43")], today=TODAY) == []


def test_two_copies_and_one_reversal_leave_one_live_copy():
    """KLN 0006814591-01: +2,746.71 twice, -2,746.71 once — already corrected."""
    found = dup.detect([E("B1", "2746.71"), E("B2", "2746.71"), E("B3", "-2746.71")],
                       today=TODAY)
    assert found == []


def test_three_copies_one_reversal_still_one_extra():
    found = dup.detect([E("B1", "743.16"), E("B2", "743.16"), E("B3", "743.16"),
                        E("B4", "-743.16")], today=TODAY)
    assert kinds(found) == [dup.EXACT]
    assert found[0].extra_copies == 1
    assert found[0].extra_amount == Decimal("743.16")


def test_open_copy_is_stop_payment_and_exposure_is_capped():
    """Acklands 9653549957: one copy paid, the second still open."""
    found = dup.detect([E("B1", "3438.88", open_=0), E("B2", "3438.88", open_="3438.88")],
                       today=TODAY)
    f = found[0]
    assert f.status == "stop_payment"
    assert f.open_exposure == Decimal("3438.88")
    # Both copies open: only ONE copy's worth is duplicate — the first is owed.
    both = dup.detect([E("B1", "544.14", open_="544.14"), E("B2", "544.14", open_="544.14")],
                      today=TODAY)[0]
    assert both.open_exposure == Decimal("544.14")


def test_same_vendor_and_number_in_two_currencies_still_breaks_the_rule():
    """Uniqueness is vendor + number; the currency is not part of it. The money
    cannot be added across currencies, so none is reported."""
    found = dup.detect([E("B1", "272.24", ccy="USD"), E("B2", "272.24", ccy="CAD")],
                       today=TODAY)
    assert kinds(found) == [dup.AMOUNT_DIFFERS]
    assert found[0].currency == dup.MIXED
    assert found[0].extra_amount == 0


def test_reversal_nets_only_within_its_currency():
    """Tenaquip 16464968-00: USD bill, CAD bill, USD credit — one live copy."""
    found = dup.detect([E("B1", "272.24", ccy="USD"), E("B2", "272.24", ccy="CAD"),
                        E("B3", "-272.24", ccy="USD")], today=TODAY)
    assert found == []


def test_invoice_number_is_matched_normalised():
    found = dup.detect([E("B1", "10", inv="ca5z82 tkzlci"), E("B2", "10", inv="CA5Z82TKZLCI")],
                       today=TODAY)
    assert kinds(found) == [dup.EXACT]


# ── amount_differs ───────────────────────────────────────────────────────────

def test_split_invoice_still_breaks_the_rule():
    """Gertex 3061177: goods and freight settled as two bills the same day.
    Finance's rule counts it (2026-09-25); a review clears it."""
    found = dup.detect([E("B1", "2418.20"), E("B2", "93.51")], today=TODAY)
    assert kinds(found) == [dup.AMOUNT_DIFFERS]
    assert found[0].status == "review"
    assert dup.AMOUNT_DIFFERS in dup.ACTIONABLE


def test_partial_credit_leaves_one_bill_not_a_split():
    """Accrue 101539: an invoice and a smaller credit against it."""
    assert dup.detect([E("B1", "1543.83"), E("B2", "-200.62")], today=TODAY) == []


def test_one_finding_per_vendor_and_number():
    """A repeated amount alongside a different one: one group, reported as the
    double entry it contains, with all three bills in it."""
    found = dup.detect([E("B1", "10"), E("B2", "10"), E("B3", "5")], today=TODAY)
    assert kinds(found) == [dup.EXACT]
    f = found[0]
    assert f.bill_nos == ["B1", "B2", "B3"]
    assert f.extra_copies == 2
    assert f.extra_amount == Decimal("10")
    assert f.amount is None


# ── the vendor half of the key ───────────────────────────────────────────────

def test_same_number_and_amount_from_two_vendors_is_not_a_duplicate():
    """Invoice numbers are the vendor's. Nielsen under two differently named
    vendor records is, by the rule finance set, two vendors."""
    found = dup.detect([E("B1", "3860.84", sc="S1", name="ACNieisen Company of Cana"),
                        E("B2", "3860.84", sc="S2", name="Nielsen Consumer LLC")],
                       today=TODAY)
    assert found == []


def test_freelancers_numbering_from_one_are_not_duplicates():
    found = dup.detect([E("B1", "500", sc="S1", inv="1", name="Joseph Tong"),
                        E("B2", "500", sc="S2", inv="1", name="Jacob Autio")], today=TODAY)
    assert found == []


def test_vendor_is_matched_by_name_not_code():
    """The same vendor keyed under two codes is still one vendor."""
    found = dup.detect([E("B1", "10", sc="S1", name="Acme Ltd"),
                        E("B2", "10", sc="S2", name="  acme   LTD ")], today=TODAY)
    assert kinds(found) == [dup.EXACT]
    assert found[0].supplier_code is None          # two codes: no single one to show
    assert found[0].vendor == "ACME LTD"


def test_wrong_vendor_reversed_and_rebilled_is_clean():
    """Acklands/Nilfisk 20419497: billed to the wrong vendor, credited there,
    billed to the right one."""
    found = dup.detect([E("B1", "3684.92", sc="ACK"), E("B2", "-3684.92", sc="ACK"),
                        E("B3", "3684.92", sc="NIL")], today=TODAY)
    assert found == []


# ── pending ──────────────────────────────────────────────────────────────────

def test_draft_repeating_an_approved_invoice_is_pending():
    found = dup.detect([E("B1", "100"), E("D1", "100", eff=False, d=date(2026, 9, 20))],
                       today=TODAY)
    assert kinds(found) == [dup.PENDING]
    f = found[0]
    assert f.status == "stop_approval"
    assert f.bill_nos == ["B1", "D1"]


def test_two_drafts_with_the_same_number_are_pending():
    """507848: on four unapproved payables at once."""
    found = dup.detect([E(f"D{i}", "50", eff=False, approve=3, d=date(2026, 8, 26))
                        for i in range(4)], today=TODAY)
    assert kinds(found) == [dup.PENDING]
    assert found[0].extra_copies == 4


def test_draft_credit_note_quoting_the_invoice_is_not_pending():
    assert dup.detect([E("B1", "100"), E("D1", "-100", eff=False, d=date(2026, 9, 20))],
                      today=TODAY) == []


def test_reentering_a_fully_reversed_bill_is_not_pending():
    found = dup.detect([E("B1", "100"), E("B2", "-100"),
                        E("D1", "100", eff=False, d=date(2026, 9, 20))], today=TODAY)
    assert found == []


def test_old_drafts_are_left_to_subledger_health():
    found = dup.detect([E("B1", "100"), E("D1", "100", eff=False, d=date(2026, 5, 1))],
                       today=TODAY)
    assert found == []


def test_dismissed_draft_is_not_raised_again():
    found = dup.detect([E("B1", "100"),
                        E("D1", "100", eff=False, d=date(2026, 9, 20), dismissed=True)],
                       today=TODAY)
    assert found == []


def test_draft_from_another_supplier_is_not_pending():
    found = dup.detect([E("B1", "100", sc="S1"),
                        E("D1", "100", sc="S2", eff=False, d=date(2026, 9, 20))], today=TODAY)
    assert found == []


# ── verdicts ─────────────────────────────────────────────────────────────────

def test_review_covers_the_bills_it_saw_and_reopens_on_a_new_copy():
    two = dup.detect([E("B1", "10"), E("B2", "10")], today=TODAY)
    review = {"bill_nos": ["B1", "B2"], "reason": "not_duplicate"}
    dup.apply_reviews(two, {two[0].key: review})
    assert two[0].review is review and not two[0].reopened

    three = dup.detect([E("B1", "10"), E("B2", "10"), E("B3", "10")], today=TODAY)
    assert three[0].key == two[0].key
    dup.apply_reviews(three, {three[0].key: review})
    assert three[0].review is None and three[0].reopened


def test_keys_are_stable_and_follow_vendor_and_number():
    a = dup.detect([E("B1", "10"), E("B2", "10")], today=TODAY)[0]
    b = dup.detect([E("B1", "10"), E("B2", "10")], today=TODAY)[0]
    c = dup.detect([E("B1", "10", sc="S9"), E("B2", "10", sc="S9")], today=TODAY)[0]
    d = dup.detect([E("B1", "10"), E("B2", "7")], today=TODAY)[0]
    assert a.key == b.key
    assert a.key != c.key                           # another vendor
    assert a.key != d.key                           # another kind


def _legacy(kind, inv, bills, key="exact:CAD:0000518:0001128981-01:22.60"):
    """A verdict as 5abc3fa3 stored it — the old key format."""
    return {"finding_key": key, "kind": kind, "invoice_no_norm": inv,
            "bill_nos": bills, "reason": "other", "note": "balanced in NC."}


def test_verdict_under_the_old_key_still_covers_its_group():
    """Production had one of these before the vendor-name rule shipped:
    KLN 0001128981-01, 22.60, reviewed 2026-09-25 20:41 UTC."""
    found = dup.detect([E("B1", "22.60", inv="0001128981-01"),
                        E("B2", "22.60", inv="0001128981-01")], today=TODAY)
    old = _legacy(dup.EXACT, "0001128981-01", ["B1", "B2"])
    dup.apply_reviews(found, {old["finding_key"]: old})
    assert found[0].review is old
    assert found[0].matched_key == old["finding_key"] != found[0].key


def test_old_verdict_does_not_cover_another_vendors_group():
    """Same invoice number, disjoint bills: another vendor's verdict."""
    found = dup.detect([E("C1", "22.60", sc="S9", inv="0001128981-01"),
                        E("C2", "22.60", sc="S9", inv="0001128981-01")], today=TODAY)
    old = _legacy(dup.EXACT, "0001128981-01", ["B1", "B2"])
    dup.apply_reviews(found, {old["finding_key"]: old})
    assert found[0].review is None and not found[0].reopened


def test_old_verdict_reopens_when_a_copy_was_added_since():
    found = dup.detect([E("B1", "22.60", inv="X"), E("B2", "22.60", inv="X"),
                        E("B3", "22.60", inv="X")], today=TODAY)
    old = _legacy(dup.EXACT, "X", ["B1", "B2"])
    dup.apply_reviews(found, {old["finding_key"]: old})
    assert found[0].review is None and found[0].reopened
    assert found[0].matched_key == old["finding_key"]


def test_old_verdict_of_another_kind_is_not_borrowed():
    """A cross_supplier verdict (a kind that no longer exists) covers nothing."""
    found = dup.detect([E("B1", "10", inv="X"), E("B2", "10", inv="X")], today=TODAY)
    old = _legacy("cross_supplier", "X", ["B1", "B2"], key="cross_supplier:CAD:*:X:10.00")
    dup.apply_reviews(found, {old["finding_key"]: old})
    assert found[0].review is None and not found[0].reopened


# ── DB-backed: seeding the mirror ────────────────────────────────────────────

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
             f"user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")


def _exec(sql, params=()):
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor(); cur.execute(sql, params)
    out = cur.fetchall() if cur.description else None
    con.close(); return out


def _bill(bill_no, amount, *, sc="S1", inv="INV-1", ccy="CAD", open_=0,
          status=(1, 1), day=date(2026, 9, 1)):
    bid = uuid.uuid4()
    when = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    _exec("insert into nc_ap_bills (id, nc_pk, bill_no, trade_type, bill_status, "
          "approve_status, supplier_code, supplier_name, currency, money, bill_date, "
          "nc_ts, synced_at, created_at, updated_at) values "
          "(%s,%s,%s,'F1-Cxx-017',%s,%s,%s,%s,%s,%s,%s,'2026-09-01 00:00:00',now(),now(),now())",
          (bid, f"PK{bill_no}", bill_no, status[0], status[1], sc, f"Supplier {sc}", ccy,
           amount, when))
    _exec("insert into nc_ap_bill_lines (id, bill_id, nc_pk, bill_no, bill_date, money_cr, "
          "money_bal, invoice_no, invoice_no_norm, supplier_code, supplier_name, nc_ts, "
          "synced_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'2026-09-01 00:00:00',now())",
          (uuid.uuid4(), bid, f"L{bill_no}", bill_no, when, amount, open_, inv,
           "".join(inv.upper().split()), sc, f"Supplier {sc}"))


@pytest.fixture
def seeded(db_session):
    _exec("delete from tasks")
    _bill("D1001", "50722.88", inv="1497")                     # paid
    _bill("D1002", "50722.88", inv="1497", open_="50722.88")   # second copy, still open
    _bill("D1003", "93.51", inv="GX1")                         # split — review only
    _bill("D1004", "2418.20", inv="GX1")
    yield


def _run(today=TODAY):
    con = psycopg2.connect(_TEST_DSN); cur = con.cursor()
    out = tasks.raise_or_clear(cur, uuid.uuid4(), today=today)
    con.commit(); con.close()
    return out


def _open_tasks():
    return _exec("select type, document_type, document_number, assigned_role, priority, "
                 "title, description from tasks where is_completed is false")


async def test_findings_read_the_mirror(db_session, seeded):
    found = await dup.findings(db_session, today=TODAY)
    assert kinds(found) == [dup.AMOUNT_DIFFERS, dup.EXACT]
    exact = next(f for f in found if f.kind == dup.EXACT)
    assert exact.open_exposure == Decimal("50722.88")


async def test_unapproved_bills_are_not_counted_as_copies(db_session):
    _bill("D2001", "100", inv="X")
    _bill("D2002", "100", inv="X", status=(-1, -1), day=date(2025, 1, 1))  # old draft
    _bill("D2003", "100", inv="X", status=(-99, 3), day=date(2026, 9, 20))  # deleted
    assert await dup.findings(db_session, today=TODAY) == []


async def test_legacy_verdict_is_honoured_and_can_be_undone(db_session, seeded):
    """The row production held, verbatim in shape: old key, same bills."""
    _exec("insert into nc_ap_invoice_dup_reviews (finding_key, kind, invoice_no_norm, "
          "currency, supplier_code, bill_nos, reason, note, reviewed_by_name, reviewed_at) "
          "values ('exact:CAD:S1:1497:50722.88', 'exact', '1497', 'CAD', 'S1', "
          "array['D1001','D1002'], 'recovered', 'old key', 'AP Clerk', now())")
    exact = next(f for f in await dup.findings(db_session, today=TODAY) if f.kind == dup.EXACT)
    assert exact.review is not None and exact.review["note"] == "old key"
    assert exact.key != "exact:CAD:S1:1497:50722.88"

    # Undo from the page (which only knows the new key) retires the old row.
    assert (await dup.unreview(db_session, exact.key, uuid.uuid4(), "AP Clerk")) == {"retired": 1}
    again = next(f for f in await dup.findings(db_session, today=TODAY) if f.kind == dup.EXACT)
    assert again.review is None
    # And a fresh verdict is stored under the new key.
    assert (await dup.review(db_session, again.key, again.bill_nos, "recovered", None,
                             uuid.uuid4(), "AP Clerk"))["applied"]
    live = _exec("select finding_key from nc_ap_invoice_dup_reviews where retired_at is null")
    assert live == [(again.key,)]


async def test_reviewing_a_reopened_legacy_group_retires_the_old_row(db_session, seeded):
    _exec("insert into nc_ap_invoice_dup_reviews (finding_key, kind, invoice_no_norm, "
          "currency, supplier_code, bill_nos, reason, reviewed_at) "
          "values ('exact:CAD:S1:1497:50722.88', 'exact', '1497', 'CAD', 'S1', "
          "array['D1001','D1002'], 'recovered', now())")
    _bill("D1009", "50722.88", inv="1497")                  # a third copy since
    f = next(f for f in await dup.findings(db_session, today=TODAY) if f.kind == dup.EXACT)
    assert f.review is None and f.reopened
    assert (await dup.review(db_session, f.key, f.bill_nos, "correcting_in_nc", None,
                             uuid.uuid4(), "AP Clerk"))["applied"]
    live = _exec("select finding_key from nc_ap_invoice_dup_reviews where retired_at is null")
    assert live == [(f.key,)]


# ── the standing task ────────────────────────────────────────────────────────

async def test_task_raised_refreshed_and_closed(db_session, seeded):
    assert _run() == "raised"
    rows = _open_tasks()
    assert len(rows) == 1
    typ, doc_type, number, role, priority, title, description = rows[0]
    assert (typ, doc_type, number, role) == (tasks.TASK_TYPE, tasks.DOC_TYPE,
                                              tasks.TASK_KEY, "ap_clerk")
    assert priority == "urgent"                  # money is still payable on a copy
    assert "50,722.88 CAD" in title
    assert dup.KIND_LABELS[dup.EXACT] in description
    # Different amounts break the rule too, so the split is in the task.
    assert dup.KIND_LABELS[dup.AMOUNT_DIFFERS] in description

    assert _run() == "refreshed"
    assert len(_open_tasks()) == 1

    # The copy is voided in NC: nothing is payable twice any more, but the
    # split still breaks the rule — the task stays, no longer urgent.
    _exec("update nc_ap_bills set bill_status=-1, approve_status=-1, "
          "bill_date='2025-01-01' where bill_no='D1002'")
    assert _run() == "refreshed"
    rows = _open_tasks()
    assert rows[0][4] == "normal"

    # Reviewed as a split -> the task closes itself.
    split = next(f for f in await dup.findings(db_session, today=TODAY))
    assert split.kind == dup.AMOUNT_DIFFERS
    assert (await dup.review(db_session, split.key, split.bill_nos, "split", None,
                             uuid.uuid4(), "AP Clerk"))["applied"]
    assert _run() == "closed"
    assert _open_tasks() == []
    assert _run() == "clean"


async def test_a_reviewed_finding_does_not_hold_the_task_open(db_session, seeded):
    assert _run() == "raised"
    f = next(f for f in await dup.findings(db_session, today=TODAY) if f.kind == dup.EXACT)
    res = await dup.review(db_session, f.key, f.bill_nos, "recovered", None,
                           uuid.uuid4(), "AP Clerk")
    assert res["applied"]
    # The split is still unreviewed, so the task stays — not urgent any more.
    assert _run() == "refreshed"
    assert _open_tasks()[0][4] == "normal"
    g = next(f for f in await dup.findings(db_session, today=TODAY) if f.review is None)
    assert (await dup.review(db_session, g.key, g.bill_nos, "split", None,
                             uuid.uuid4(), "AP Clerk"))["applied"]
    assert _run() == "closed"


async def test_task_failure_does_not_poison_the_transaction(db_session, seeded):
    con = psycopg2.connect(_TEST_DSN); cur = con.cursor()
    cur.execute("create temp table probe (x int)")
    orig = dup._ENTRIES_SQL
    try:
        dup._ENTRIES_SQL = "select * from a_table_that_is_not_there"
        assert tasks.raise_or_clear(cur, uuid.uuid4()) == "failed"
        cur.execute("insert into probe values (1)")
        con.commit()
    finally:
        dup._ENTRIES_SQL = orig
        con.close()


# ── API ──────────────────────────────────────────────────────────────────────

def _h(role="ap_clerk"):
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": role, "full_name": "Test Clerk",
                      "exp": datetime.now(timezone.utc).timestamp() + 3600},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    app.dependency_overrides.clear()


URL = "/finance/v1/ap-duplicate-invoices"


async def test_api_lists_and_summarises(client, seeded):
    async with client as c:
        r = await c.get(URL, headers=_h())
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        exact = next(f for f in body["findings"] if f["kind"] == "exact")
        assert exact["status"] == "stop_payment"
        assert exact["open_exposure"] == "50722.88"
        assert [b["bill_no"] for b in exact["bills"]] == ["D1001", "D1002"]
        assert body["summary"]["exact"]["unreviewed"] == 1
        assert body["summary"]["exact"]["by_currency"]["CAD"]["open_exposure"] == "50722.88"

        r = await c.get(URL, params={"kind": "exact"}, headers=_h())
        assert r.json()["total"] == 1
        # The summary does not follow the filter.
        assert r.json()["summary"]["amount_differs"]["unreviewed"] == 1


async def test_api_review_then_unreview(client, seeded):
    async with client as c:
        exact = next(f for f in (await c.get(URL, headers=_h())).json()["findings"]
                     if f["kind"] == "exact")
        r = await c.post(f"{URL}/review", headers=_h(), json={
            "key": exact["key"], "bill_nos": exact["bill_nos"], "reason": "recovered",
            "note": "Credit note CN-1 received"})
        assert r.status_code == 200, r.text

        body = (await c.get(URL, headers=_h())).json()
        assert all(f["kind"] != "exact" for f in body["findings"])
        assert body["summary"]["exact"]["reviewed"] == 1
        done = (await c.get(URL, params={"reviewed": "yes"}, headers=_h())).json()
        assert done["findings"][0]["review"]["reviewed_by_name"] == "Test Clerk"

        # Twice is refused rather than stacking a second verdict.
        again = await c.post(f"{URL}/review", headers=_h(), json={
            "key": exact["key"], "bill_nos": exact["bill_nos"], "reason": "recovered"})
        assert again.status_code == 409

        assert (await c.post(f"{URL}/unreview", headers=_h(),
                             json={"key": exact["key"]})).json() == {"retired": 1}
        assert (await c.get(URL, params={"kind": "exact"}, headers=_h())).json()["total"] == 1


async def test_api_review_refuses_a_group_that_moved(client, seeded):
    async with client as c:
        exact = next(f for f in (await c.get(URL, headers=_h())).json()["findings"]
                     if f["kind"] == "exact")
        _bill("D1005", "50722.88", inv="1497")          # a third copy lands meanwhile
        r = await c.post(f"{URL}/review", headers=_h(), json={
            "key": exact["key"], "bill_nos": exact["bill_nos"], "reason": "not_duplicate"})
        assert r.status_code == 409
        assert r.json()["detail"]["bill_nos"] == ["D1001", "D1002", "D1005"]


async def test_api_rejects_bad_input(client, seeded):
    async with client as c:
        assert (await c.get(URL, params={"kind": "nope"}, headers=_h())).status_code == 422
        exact = next(f for f in (await c.get(URL, headers=_h())).json()["findings"]
                     if f["kind"] == "exact")
        for payload in ({"key": exact["key"], "bill_nos": exact["bill_nos"], "reason": "bogus"},
                        {"key": exact["key"], "bill_nos": exact["bill_nos"], "reason": "other"},
                        {"key": exact["key"], "bill_nos": ["D1001"], "reason": "split"},
                        {"bill_nos": exact["bill_nos"], "reason": "split"}):
            assert (await c.post(f"{URL}/review", headers=_h(), json=payload)).status_code == 422


async def test_api_is_finance_only(client, seeded):
    """Denied for a non-finance role — AND admitted for each finance role, or
    "everyone is refused" would pass this test too."""
    async with client as c:
        assert (await c.get(URL)).status_code in (401, 403)
        assert (await c.get(URL, headers=_h("requester"))).status_code == 403
        assert (await c.post(f"{URL}/unreview", headers=_h("requester"),
                             json={"key": "x"})).status_code == 403
        for role in ("ap_clerk", "payment_officer", "finance_manager", "finance_bp"):
            assert (await c.get(URL, headers=_h(role))).status_code == 200, role


# ── wired into the AP sync ───────────────────────────────────────────────────

async def test_ap_sync_run_raises_the_task(db_session, monkeypatch):
    """A correct check that the sync never calls leaves AP as blind as before."""
    from app.services import nc_ap_sync as sync

    _exec("delete from tasks")
    monkeypatch.setattr(sync, "nc_configured", lambda: True)

    def bill(pk, no):
        return (pk, no, "F1-Cxx-017", "F1", 1, "yf", "2026", "09",
                "2026-09-01 00:00:00", "2026-09-01 00:00:00", "2026-09-01 00:00:00",
                1, 1, "CUR", "10.00", "10.00", "u", "2026-09-01 00:00:00")

    def line(pk, bill_pk):
        return (pk, bill_pk, 1, "10.00", "10.00", "10.00", "10.00", "INV-77",
                None, None, None, "2202", None, None, None, None, "SUP", None, None,
                None, None, None, "2026-09-01 00:00:00")

    def fetch(_watermark):
        return sync.NcApExtract(
            bills=[bill("P1", "D19001"), bill("P2", "D19002")],
            lines=[line("L1", "P1"), line("L2", "P2")],
            suppliers={"SUP": ("0000999", "Twice Billed Ltd")},
            currencies={"CUR": "CAD"}, max_ts="2026-09-01 00:00:00",
            nc_totals={})

    sync.start_run("full", None, fetch=fetch, pg_dsn=_TEST_DSN,
                   confirm=sync.FULL_CONFIRM, run_worker=True)
    status = _exec("select status, error from nc_ap_sync_runs order by started_at desc limit 1")
    assert status[0][0] == "success", status
    rows = _open_tasks()
    assert len(rows) == 1 and rows[0][0] == tasks.TASK_TYPE
    # Both copies open: one copy's worth is duplicate, the first is owed.
    assert "10.00 CAD" in rows[0][5]
