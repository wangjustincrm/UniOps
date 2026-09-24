"""Bank Reconciliation v2 through the API — open, match, sign off, report.

The scenario is July 2026 in miniature, with the shapes that make the real month
hard kept intact:

  - a merged batch: one statement line, one payment file, three vendor payments,
    three ledger lines
  - a bill payment joined by its confirmation number
  - an internal transfer as a credit
  - a bank charge, one to one
  - a statement line whose ledger side is two lines and has no payment file

The ledger side is built the way nc_sync builds it — journal_voucher_lines on
100201 with a bank_account_id — because that column IS the feature's
prerequisite, and a test that faked the book side would not prove the wiring.
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
from app.models.bank import UNMATCHED, BankAccount, BankTransaction
from app.models.bank_recon import BankPaymentAdvice, BankPaymentAdviceLine
from app.models.coa import ChartOfAccount
from app.models.journal_voucher import POSTED, JournalVoucher, JournalVoucherLine
from app.models.nc_bank_account import NcBankAccount

D = Decimal
JUL = lambda d: date(2026, 7, d)  # noqa: E731


def _h(role="finance_manager"):
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": role, "name": "Test Preparer",
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override():
        yield db_session
    app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def scene(db_session):
    """One bank account, wired to NC, with both sides of a small July."""
    db_session.add_all([
        ChartOfAccount(code="1002", name="Cash on Bank", account_type="asset",
                       normal_balance="debit", is_postable=False),
        ChartOfAccount(code="100201", name="Checking", account_type="asset",
                       normal_balance="debit", is_postable=True, parent_code="1002"),
        ChartOfAccount(code="2202", name="Accounts payable", account_type="liability",
                       normal_balance="credit", is_postable=True),
        ChartOfAccount(code="6603", name="Financial expenses", account_type="expense",
                       normal_balance="debit", is_postable=True),
    ])
    nc = NcBankAccount(nc_pk="NCBANK1", code="1033760", acc_num="1033760",
                       name="RBC CAD Chequing", acc_name="RBC", bank_name="RBC-York Street",
                       currency="CAD")
    acct = BankAccount(name="RBC CAD", bank_name="RBC", account_masked="3760",
                       currency="CAD", ledger_account_code="100201",
                       nc_bank_account_code="1033760")
    db_session.add_all([nc, acct])
    await db_session.flush()

    other = NcBankAccount(nc_pk="NCBANK2", code="1060", name="BOC CAD", currency="CAD")
    db_session.add(other)
    await db_session.flush()

    async def voucher(num, day, lines, period="2026-07", month=7):
        jv = JournalVoucher(jv_number=f"JV-2026{month:02d}-{num:04d}", voucher_word="JV",
                            voucher_date=date(2026, month, day), fiscal_period=period,
                            status=POSTED, nc_source_pk=f"NCPK{num}", source_service="nc")
        db_session.add(jv)
        await db_session.flush()
        for i, (acc, dr, cr, summary, bank_id) in enumerate(lines, start=1):
            db_session.add(JournalVoucherLine(
                jv_id=jv.id, line_no=i, account_code=acc, summary=summary,
                orig_debit=D(dr), orig_credit=D(cr),
                local_debit=D(dr), local_credit=D(cr), currency="CAD",
                bank_account_id=bank_id))
        await db_session.flush()
        return jv

    B, O = nc.id, other.id
    # June activity, carried into July as the opening balance. Dated in JUNE, not
    # on 1 July: a voucher inside the period is period activity, not an opening.
    await voucher(1, 30, [("100201", "1000.00", "0", "Opening funding", B),
                          ("2202", "0", "1000.00", "Opening funding", None)],
                  period="2026-06", month=6)
    # a batch of three vendor payments (bank date 02 Jul, book date 03 Jul)
    await voucher(10, 3, [("100201", "0", "340.75", "Alpha SupplyEFT-$340.75", B),
                          ("2202", "340.75", "0", "Alpha Supply", None)])
    await voucher(11, 3, [("100201", "0", "11997.21", "Beta SteelEFT-$11997.21", B),
                          ("2202", "11997.21", "0", "Beta Steel", None)])
    await voucher(12, 3, [("100201", "0", "765.74", "Gamma FreightEFT-$765.74", B),
                          ("2202", "765.74", "0", "Gamma Freight", None)])
    # a bill payment
    await voucher(20, 3, [("100201", "0", "31.64", "Zeta Propane eft $31.64", B),
                          ("2202", "31.64", "0", "Zeta Propane", None)])
    # an internal transfer: BOTH legs on 100201, told apart only by the aux
    await voucher(30, 7, [("100201", "400000.00", "0", "BOC transfer to RBC CAD", B),
                          ("100201", "0", "400000.00", "BOC transfer to RBC CAD", O)])
    # a bank charge
    await voucher(40, 7, [("100201", "0", "455.60", "RBC CAD activity fee", B),
                          ("6603", "455.60", "0", "RBC CAD activity fee", None)])
    # two lines with no payment file, summing to one statement line
    await voucher(50, 31, [("100201", "0", "28209.32", "Elevator CoEFT-$28209.32", B),
                           ("2202", "28209.32", "0", "Elevator Co", None)])
    await voucher(51, 30, [("100201", "0", "708.76", "Freight CoEFT-$708.76", B),
                           ("2202", "708.76", "0", "Freight Co", None)])

    # the statement side (an import that did not need the AI)
    def txn(day, desc, amount, seq, balance=None):
        return BankTransaction(
            bank_account_id=acct.id, txn_date=JUL(day), description=desc,
            amount=D(amount), currency="CAD", status=UNMATCHED, sort_seq=seq,
            running_balance=D(balance) if balance else None,
            import_hash=f"h{seq}-{uuid.uuid4()}")

    db_session.add_all([
        txn(2, "Bill payment - 8642 ZETA PROP", "-31.64", 1),
        txn(2, "Direct Deposits (PDS) service total GRADS9804420000", "-13103.70", 2),
        txn(7, "Funds transfer credit TT 1/CANADA ROY", "400000.00", 3),
        txn(7, "Activity fee", "-455.60", 4),
        txn(31, "Direct Deposits (PDS) service total GRADS9804420000", "-28918.08", 5),
    ])

    # the payment files
    batch = BankPaymentAdvice(
        bank_account_id=acct.id, advice_kind="pds_batch", advice_date=JUL(2),
        currency="CAD", client_number="9804420000", total=D("13103.70"),
        line_count=3, printed_total=D("13103.70"), printed_count=3, tie_ok=True,
        source_filename="7.2.pdf", source_sha256="sha-batch", status="imported")
    bill = BankPaymentAdvice(
        bank_account_id=acct.id, advice_kind="bill_payment", advice_date=JUL(2),
        currency="CAD", confirmation_number="8642", total=D("31.64"), line_count=1,
        tie_ok=True, source_filename="7.2-bill.pdf", source_sha256="sha-bill",
        status="imported")
    db_session.add_all([batch, bill])
    await db_session.flush()
    for i, (name, amt) in enumerate(
            [("Alpha Supply", "340.75"), ("Beta Steel", "11997.21"),
             ("Gamma Freight", "765.74")], start=1):
        db_session.add(BankPaymentAdviceLine(
            advice_id=batch.id, seq=i, payee_name=name, amount=D(amt), currency="CAD"))
    db_session.add(BankPaymentAdviceLine(
        advice_id=bill.id, seq=1, payee_name="ZETA PROPANE", amount=D("31.64"),
        currency="CAD"))
    await db_session.flush()
    return {"account": acct, "nc": nc, "batch": batch, "bill": bill}


# ── the book side ─────────────────────────────────────────────────────────────

async def test_book_side_is_scoped_to_one_bank_account(client, scene):
    """The transfer posts BOTH legs to 100201. Only the aux tells them apart, and
    without it the 400,000 would net to zero and vanish."""
    r = await client.get(f"/finance/v1/bank-recon/{scene['account'].id}/book"
                         "?date_from=2026-07-01&date_to=2026-07-31", headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bank_account"] == "1033760/RBC CAD Chequing"
    amounts = sorted(D(l["amount"]) for l in body["lines"])
    assert D("400000.00") in amounts            # our leg, not the BOC one
    assert amounts.count(D("-400000.00")) == 0
    assert body["opening"] == "1000.00"         # June carried in
    kinds = {l["contra_kind"] for l in body["lines"]}
    assert {"ap", "bank_transfer", "bank_fee"} <= kinds


async def test_an_unmapped_account_says_so_instead_of_looking_empty(client, db_session):
    acct = BankAccount(name="Unmapped", bank_name="X", currency="CAD")
    db_session.add(acct)
    await db_session.flush()
    r = await client.get(f"/finance/v1/bank-recon/{acct.id}/book"
                         "?date_from=2026-07-01&date_to=2026-07-31", headers=_h())
    assert r.status_code == 409
    assert "not linked to an NC bank account" in r.json()["detail"]


# ── the session ───────────────────────────────────────────────────────────────

async def _open(client, acct):
    r = await client.post(f"/finance/v1/bank-recon/{acct.id}/reconciliations",
                          json={"period_start": "2026-07-01", "period_end": "2026-07-31"},
                          headers=_h())
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def test_the_whole_period_reconciles_to_zero(client, scene):
    rid = await _open(client, scene["account"])
    r = await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match",
                          headers=_h())
    assert r.status_code == 200, r.text
    got = r.json()

    assert got["bank_lines_cleared"] == 5
    assert got["book_lines_cleared"] == 8
    assert set(got["by_method"]) == {"confirmation_no", "advice_total", "direct",
                                     "book_subset"}
    s = got["summary"]
    assert s["outstanding_bank_lines"] == 0
    assert s["outstanding_book_lines"] == 0
    # book closing = 1000 opening - 13103.70 - 31.64 + 400000 - 455.60 - 28918.08
    assert s["book_closing"] == "358490.98"
    # the no-payment-file line is cleared AND reported
    assert [f["kind"] for f in got["findings"]] == ["no_advice"]


async def test_auto_match_is_idempotent(client, scene):
    rid = await _open(client, scene["account"])
    first = (await client.post(
        f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match", headers=_h())).json()
    second = (await client.post(
        f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match", headers=_h())).json()
    assert first["matched_groups"] > 0
    assert second["matched_groups"] == 0
    assert second["summary"]["outstanding_bank_lines"] == 0


async def test_the_workbench_payload_carries_both_sides_and_the_groups(client, scene):
    rid = await _open(client, scene["account"])
    await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match",
                      headers=_h())
    r = await client.get(f"/finance/v1/bank-recon/reconciliations/{rid}", headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["bank_lines"]) == 5 and all(b["cleared"] for b in body["bank_lines"])
    assert len(body["book_lines"]) == 8 and all(b["cleared"] for b in body["book_lines"])
    batch_group = next(m for m in body["matches"] if m["method"] == "advice_total")
    assert len(batch_group["book_lines"]) == 3        # one bank line, three ledger lines
    assert batch_group["advice_id"] == str(scene["batch"].id)
    # every ledger reference carries NC's natural key, for the post-sync healer
    assert all(b["nc_voucher_pk"] for b in batch_group["book_lines"])


async def test_the_vendor_breakdown_links_through_to_the_ledger(client, scene):
    """What finance opens the payment-file PDF for today."""
    rid = await _open(client, scene["account"])
    await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match",
                      headers=_h())
    r = await client.get(f"/finance/v1/bank-recon/advices/{scene['batch'].id}/lines",
                         headers=_h())
    lines = r.json()
    assert len(lines) == 3
    assert all(l["matched_jv_line_id"] for l in lines)


# ── manual matching ───────────────────────────────────────────────────────────

async def test_a_manual_group_must_balance(client, scene):
    rid = await _open(client, scene["account"])
    book = (await client.get(f"/finance/v1/bank-recon/{scene['account'].id}/book"
                             "?date_from=2026-07-01&date_to=2026-07-31",
                             headers=_h())).json()["lines"]
    page = (await client.get(f"/finance/v1/bank-recon/reconciliations/{rid}",
                             headers=_h())).json()
    fee_txn = next(b for b in page["bank_lines"] if b["amount"] == "-455.60")
    wrong = next(b for b in book if b["amount"] == "-340.75")
    r = await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/matches",
                          json={"bank_transaction_ids": [fee_txn["id"]],
                                "jv_line_ids": [wrong["jv_line_id"]]}, headers=_h())
    assert r.status_code == 422
    assert "do not balance" in r.json()["detail"]


async def test_a_balanced_manual_group_clears_and_can_be_undone(client, scene):
    rid = await _open(client, scene["account"])
    book = (await client.get(f"/finance/v1/bank-recon/{scene['account'].id}/book"
                             "?date_from=2026-07-01&date_to=2026-07-31",
                             headers=_h())).json()["lines"]
    page = (await client.get(f"/finance/v1/bank-recon/reconciliations/{rid}",
                             headers=_h())).json()
    fee_txn = next(b for b in page["bank_lines"] if b["amount"] == "-455.60")
    fee_book = next(b for b in book if b["amount"] == "-455.60")
    r = await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/matches",
                          json={"bank_transaction_ids": [fee_txn["id"]],
                                "jv_line_ids": [fee_book["jv_line_id"]],
                                "note": "bank charge"}, headers=_h())
    assert r.status_code == 200, r.text
    match_id = r.json()["id"]

    again = await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/matches",
                              json={"bank_transaction_ids": [fee_txn["id"]],
                                    "jv_line_ids": [fee_book["jv_line_id"]]}, headers=_h())
    assert again.status_code == 422 and "already cleared" in again.json()["detail"]

    undo = await client.delete(
        f"/finance/v1/bank-recon/reconciliations/{rid}/matches/{match_id}", headers=_h())
    assert undo.status_code == 200
    page2 = (await client.get(f"/finance/v1/bank-recon/reconciliations/{rid}",
                              headers=_h())).json()
    assert not next(b for b in page2["bank_lines"] if b["id"] == fee_txn["id"])["cleared"]


async def test_candidates_are_offered_for_a_line_a_human_must_clear(client, scene):
    rid = await _open(client, scene["account"])
    page = (await client.get(f"/finance/v1/bank-recon/reconciliations/{rid}",
                             headers=_h())).json()
    fee = next(b for b in page["bank_lines"] if b["amount"] == "-455.60")
    r = await client.get(
        f"/finance/v1/bank-recon/reconciliations/{rid}/candidates/{fee['id']}", headers=_h())
    assert r.status_code == 200
    got = r.json()
    assert got and got[0]["exact"] is True and got[0]["amount"] == "-455.60"


# ── sign-off ──────────────────────────────────────────────────────────────────

async def test_finalize_refuses_while_the_statement_is_missing(client, scene):
    """No statement row means no statement_verified, and an unverified period
    cannot be signed off."""
    rid = await _open(client, scene["account"])
    await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match",
                      headers=_h())
    r = await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/finalize",
                          headers=_h())
    assert r.status_code == 422
    assert "not been verified" in r.json()["detail"] or "does not reconcile" in r.json()["detail"]


async def _with_statement(db_session, acct):
    from app.models.bank_recon import BankStatement
    st = BankStatement(
        bank_account_id=acct.id, period_start=date(2026, 6, 30),
        period_end=date(2026, 7, 31), currency="CAD",
        opening_balance=D("1000.00"), closing_balance=D("358490.98"),
        total_debits=D("42509.02"), total_credits=D("400000.00"),
        debit_count=4, credit_count=1, verified=True, status="imported",
        parse_method="ai")
    db_session.add(st)
    await db_session.flush()
    return st


async def test_a_signed_off_period_is_frozen_and_reports_from_its_snapshot(
        client, scene, db_session):
    await _with_statement(db_session, scene["account"])
    rid = await _open(client, scene["account"])
    await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match",
                      headers=_h())
    r = await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/finalize",
                          headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "finalized"

    blocked = await client.post(
        f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match", headers=_h())
    assert blocked.status_code == 409 and "finalized" in blocked.json()["detail"]

    pdf = await client.get(f"/finance/v1/bank-recon/reconciliations/{rid}/report",
                           headers=_h())
    assert pdf.status_code == 200
    assert pdf.content[:5] == b"%PDF-"
    assert "attachment" in pdf.headers["content-disposition"]

    xlsx = await client.get(f"/finance/v1/bank-recon/reconciliations/{rid}/report?fmt=xlsx",
                            headers=_h())
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"

    reopened = await client.post(
        f"/finance/v1/bank-recon/reconciliations/{rid}/reopen", headers=_h())
    assert reopened.status_code == 200 and reopened.json()["status"] == "open"


async def test_finalize_refuses_a_non_zero_difference(client, scene, db_session):
    from app.models.bank_recon import BankStatement
    db_session.add(BankStatement(
        bank_account_id=scene["account"].id, period_start=date(2026, 6, 30),
        period_end=date(2026, 7, 31), currency="CAD", opening_balance=D("1000.00"),
        closing_balance=D("999999.00"), verified=True, status="imported",
        parse_method="ai"))
    await db_session.flush()
    rid = await _open(client, scene["account"])
    await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match",
                      headers=_h())
    r = await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/finalize",
                          headers=_h())
    assert r.status_code == 422
    assert "does not reconcile" in r.json()["detail"]


# ── surviving an NC re-sync ───────────────────────────────────────────────────

async def test_matches_survive_a_full_nc_resync(client, scene, db_session):
    """A full nc_sync deletes every nc-sourced voucher and regenerates the line
    ids. The SET NULL foreign keys keep the match rows; the healer re-points them
    from NC's natural key."""
    from sqlalchemy import delete, select

    from app.models.bank_recon import BankReconMatchBook

    rid = await _open(client, scene["account"])
    await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match",
                      headers=_h())
    before = (await db_session.execute(select(BankReconMatchBook))).scalars().all()
    assert before and all(b.jv_line_id for b in before)
    n = len(before)

    # what nc_sync does, then re-inserts the same vouchers with new line ids
    old = (await db_session.execute(
        select(JournalVoucher).where(JournalVoucher.nc_source_pk.is_not(None))
    )).scalars().all()
    payload = []
    for jv in old:
        lines = (await db_session.execute(
            select(JournalVoucherLine).where(JournalVoucherLine.jv_id == jv.id)
        )).scalars().all()
        payload.append((jv.nc_source_pk, jv.jv_number, jv.voucher_date, jv.fiscal_period,
                        [(l.line_no, l.account_code, l.summary, l.orig_debit,
                          l.orig_credit, l.local_debit, l.local_credit,
                          l.bank_account_id) for l in lines]))
    await db_session.execute(
        delete(JournalVoucher).where(JournalVoucher.nc_source_pk.is_not(None)))
    await db_session.flush()

    # The FK fires in the DATABASE. Without expiring, the session hands back the
    # rows it loaded before the delete, still carrying their old jv_line_id — the
    # assertion below would pass for the wrong reason.
    db_session.expire_all()
    orphaned = (await db_session.execute(select(BankReconMatchBook))).scalars().all()
    assert len(orphaned) == n, "the match rows must survive the delete"
    assert all(b.jv_line_id is None for b in orphaned), "SET NULL, never CASCADE"

    for pk, num, vdate, period, lines in payload:
        jv = JournalVoucher(jv_number=num, voucher_word="JV", voucher_date=vdate,
                            fiscal_period=period, status=POSTED, nc_source_pk=pk,
                            source_service="nc")
        db_session.add(jv)
        await db_session.flush()
        for (no, acc, summary, odr, ocr, ldr, lcr, bank_id) in lines:
            db_session.add(JournalVoucherLine(
                jv_id=jv.id, line_no=no, account_code=acc, summary=summary,
                orig_debit=odr, orig_credit=ocr, local_debit=ldr, local_credit=lcr,
                currency="CAD", bank_account_id=bank_id))
    await db_session.flush()

    healed = await client.post("/finance/v1/bank-recon/heal", headers=_h())
    assert healed.status_code == 200, healed.text
    assert healed.json() == {"orphans": n, "healed": n, "unresolved": 0}

    page = (await client.get(f"/finance/v1/bank-recon/reconciliations/{rid}",
                             headers=_h())).json()
    assert page["summary"]["outstanding_bank_lines"] == 0


async def test_the_healer_skips_finalized_periods(client, scene, db_session):
    """A signed-off reconciliation must not silently acquire different ledger
    rows — its snapshot is the record."""
    from sqlalchemy import delete, select

    from app.models.bank_recon import BankReconMatchBook

    await _with_statement(db_session, scene["account"])
    rid = await _open(client, scene["account"])
    await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match",
                      headers=_h())
    assert (await client.post(
        f"/finance/v1/bank-recon/reconciliations/{rid}/finalize",
        headers=_h())).status_code == 200

    await db_session.execute(
        delete(JournalVoucher).where(JournalVoucher.nc_source_pk.is_not(None)))
    await db_session.flush()
    healed = await client.post("/finance/v1/bank-recon/heal", headers=_h())
    assert healed.json()["orphans"] == 0          # skipped, not touched

    rows = (await db_session.execute(select(BankReconMatchBook))).scalars().all()
    assert rows and all(r.nc_voucher_pk for r in rows)   # the natural key is still there


# ── access ────────────────────────────────────────────────────────────────────

async def test_a_reader_cannot_change_a_reconciliation(client, scene):
    """Paired with the positive cases above: `ap_clerk` reads finance data but is
    not allowed to clear or sign off a bank account."""
    rid = await _open(client, scene["account"])
    r = await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match",
                          headers=_h(role="ap_clerk"))
    assert r.status_code == 403
    ok = await client.get(f"/finance/v1/bank-recon/reconciliations/{rid}",
                          headers=_h(role="ap_clerk"))
    assert ok.status_code == 200, "a reader must still be able to READ it"


# ── the import endpoints ──────────────────────────────────────────────────────
#
# The parsers have their own tests and are measured against the real July files by
# scripts/validate_bank_docs.py. What was untested is the HOP: multipart upload ->
# crud -> database -> file retention -> response. That is the first thing anyone
# touches, so it gets covered here with the parse stubbed — patching the
# module-bound name the crud actually calls, not the definition site.

from datetime import date as _date  # noqa: E402
from decimal import Decimal as _D  # noqa: E402


def _parsed_statement(verified=True, errors=None):
    from app.services.bank_statement_parse import ParsedStatement, StatementLine
    return ParsedStatement(
        period_start=_date(2026, 6, 30), period_end=_date(2026, 7, 31),
        opening_balance=_D("1000.00"), closing_balance=_D("968.36"),
        lines=[StatementLine(seq=1, txn_date=_date(2026, 7, 2),
                             description="Bill payment - 8642 ZETA PROP",
                             amount=_D("-31.64"), running_balance=_D("968.36"))],
        currency="CAD", account_no="02705 103-376-0",
        printed_total_debits=_D("31.64"), printed_total_credits=_D("0"),
        printed_debit_count=1, printed_credit_count=0,
        parse_method="ai", parse_model="claude-haiku-4-5-20251001",
        verified=verified, verify_errors=errors or [],
        raw_payload={"stub": True})


def _parsed_advice(tie_ok=True):
    from app.services.bank_advice_parse import AdviceLine, ParsedAdvice
    return ParsedAdvice(
        kind="pds_batch", advice_date=_date(2026, 7, 2), currency="CAD",
        lines=[AdviceLine(seq=1, payee_code="ALPHA", payee_name="Alpha Supply",
                          currency="CAD", amount=_D("340.75"))],
        client_number="9804420000", printed_total=_D("340.75"), printed_count=1,
        tie_ok=tie_ok, tie_error=None if tie_ok else "lines sum to 340.75 but the file prints 999.00",
    )


async def test_uploading_a_statement_stores_it_and_its_lines(client, scene, monkeypatch):
    from app.services import bank_statement_parse
    monkeypatch.setattr(bank_statement_parse, "parse_statement",
                        lambda data, filename="": _parsed_statement())
    r = await client.post(
        f"/finance/v1/bank-recon/{scene['account'].id}/statements",
        files={"file": ("RBC.pdf", b"%PDF-1.4 stub", "application/pdf")}, headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] is True and body["lines"] == 1 and body["imported"] == 1
    # No file server in the test environment: retention fails, is REPORTED, and does
    # not take the import down with it.
    assert body["retention_warning"]

    listed = (await client.get(f"/finance/v1/bank-recon/{scene['account'].id}/statements",
                               headers=_h())).json()
    assert len(listed) == 1
    assert listed[0]["opening_balance"] == "1000.00"
    assert listed[0]["has_document"] is False       # honest about what was kept


async def test_an_unverified_statement_is_kept_with_its_reasons(client, scene, monkeypatch):
    """Storing it is the point: the errors name the line to fix, and throwing the
    parse away would leave a human with "it failed" and nothing to look at. The
    reconciliation is what refuses to use it."""
    from app.services import bank_statement_parse
    monkeypatch.setattr(
        bank_statement_parse, "parse_statement",
        lambda data, filename="": _parsed_statement(
            verified=False, errors=["Line 5 (BR TO BR): the statement shows 530858.97 here"]))
    r = await client.post(
        f"/finance/v1/bank-recon/{scene['account'].id}/statements",
        files={"file": ("RBC.pdf", b"%PDF-1.4 stub", "application/pdf")}, headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["verified"] is False
    assert "BR TO BR" in r.json()["verify_errors"][0]


async def test_re_importing_a_period_supersedes_rather_than_duplicating(
        client, scene, monkeypatch):
    from app.services import bank_statement_parse
    monkeypatch.setattr(bank_statement_parse, "parse_statement",
                        lambda data, filename="": _parsed_statement())
    url = f"/finance/v1/bank-recon/{scene['account'].id}/statements"
    first = (await client.post(url, files={"file": ("a.pdf", b"%PDF-1", "application/pdf")},
                               headers=_h())).json()
    second = (await client.post(url, files={"file": ("b.pdf", b"%PDF-2", "application/pdf")},
                                headers=_h())).json()
    assert second["superseded"] == first["statement_id"]
    # the line is re-pointed, not duplicated — so matches made against it survive
    assert second["imported"] == 0 and second["reused"] == 1
    live = [s for s in (await client.get(url, headers=_h())).json()
            if s["status"] == "imported"]
    assert len(live) == 1


async def test_an_unreadable_statement_is_a_422_not_a_500(client, scene, monkeypatch):
    from app.services import bank_statement_parse
    from app.services.bank_statement_parse import StatementUnparseable

    def boom(data, filename=""):
        raise StatementUnparseable("Could not read this statement PDF.")
    monkeypatch.setattr(bank_statement_parse, "parse_statement", boom)
    r = await client.post(
        f"/finance/v1/bank-recon/{scene['account'].id}/statements",
        files={"file": ("x.pdf", b"not a pdf", "application/pdf")}, headers=_h())
    assert r.status_code == 422
    assert "Could not read" in r.json()["detail"]


async def test_an_ai_outage_is_a_503_and_says_it_is_not_your_file(client, scene, monkeypatch):
    """The distinction that mattered in 2026-09: an account-level limit told a
    clerk their document was unreadable when nothing was wrong with it."""
    from app.services import bank_statement_parse

    def limit(data, filename=""):
        raise RuntimeError("Statement reading is temporarily unavailable — the AI "
                           "service account has reached a usage or billing limit. "
                           "This is NOT a problem with your file.")
    monkeypatch.setattr(bank_statement_parse, "parse_statement", limit)
    r = await client.post(
        f"/finance/v1/bank-recon/{scene['account'].id}/statements",
        files={"file": ("x.pdf", b"%PDF", "application/pdf")}, headers=_h())
    assert r.status_code == 503
    assert "NOT a problem with your file" in r.json()["detail"]


async def test_uploading_many_payment_files_at_once(client, scene, monkeypatch):
    """A month is ~20 files. One at a time is how they end up not uploaded."""
    from app.services import bank_advice_parse
    monkeypatch.setattr(bank_advice_parse, "parse_advice",
                        lambda data: _parsed_advice())
    r = await client.post(
        f"/finance/v1/bank-recon/{scene['account'].id}/advices",
        files=[("files", ("7.2.pdf", b"%PDF-A", "application/pdf")),
               ("files", ("7.6.pdf", b"%PDF-B", "application/pdf"))], headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 2 and body["failed"] == 0


async def test_the_same_payment_file_twice_is_one_advice(client, scene, monkeypatch):
    from app.services import bank_advice_parse
    monkeypatch.setattr(bank_advice_parse, "parse_advice", lambda data: _parsed_advice())
    url = f"/finance/v1/bank-recon/{scene['account'].id}/advices"
    await client.post(url, files=[("files", ("7.2.pdf", b"%PDF-SAME", "application/pdf"))],
                      headers=_h())
    again = (await client.post(
        url, files=[("files", ("7.2-renamed.pdf", b"%PDF-SAME", "application/pdf"))],
        headers=_h())).json()
    assert again["duplicates"] == 1 and again["imported"] == 0


async def test_a_payment_file_that_does_not_tie_is_flagged_not_silently_used(
        client, scene, monkeypatch):
    from app.services import bank_advice_parse
    monkeypatch.setattr(bank_advice_parse, "parse_advice",
                        lambda data: _parsed_advice(tie_ok=False))
    r = await client.post(
        f"/finance/v1/bank-recon/{scene['account'].id}/advices",
        files=[("files", ("bad.pdf", b"%PDF", "application/pdf"))], headers=_h())
    body = r.json()
    assert len(body["not_tied"]) == 1
    assert "prints" in body["not_tied"][0]["tie_error"]


async def test_one_bad_file_does_not_lose_the_rest_of_the_batch(client, scene, monkeypatch):
    from app.services import bank_advice_parse
    from app.services.bank_advice_parse import AdviceUnparseable
    seen = {"n": 0}

    def sometimes(data):
        seen["n"] += 1
        if seen["n"] == 1:
            raise AdviceUnparseable("This does not look like a bank payment file.")
        return _parsed_advice()
    monkeypatch.setattr(bank_advice_parse, "parse_advice", sometimes)
    r = await client.post(
        f"/finance/v1/bank-recon/{scene['account'].id}/advices",
        files=[("files", ("junk.pdf", b"%PDF-1", "application/pdf")),
               ("files", ("good.pdf", b"%PDF-2", "application/pdf"))], headers=_h())
    body = r.json()
    assert body["failed"] == 1 and body["imported"] == 1
    bad = next(x for x in body["results"] if x.get("error"))
    assert bad["filename"] == "junk.pdf"


async def test_an_empty_upload_is_refused(client, scene):
    r = await client.post(
        f"/finance/v1/bank-recon/{scene['account'].id}/statements",
        files={"file": ("empty.pdf", b"", "application/pdf")}, headers=_h())
    assert r.status_code == 422


async def test_a_reader_cannot_import(client, scene):
    r = await client.post(
        f"/finance/v1/bank-recon/{scene['account'].id}/statements",
        files={"file": ("x.pdf", b"%PDF", "application/pdf")}, headers=_h(role="ap_clerk"))
    assert r.status_code == 403


# ── the NC account picker ─────────────────────────────────────────────────────

async def test_nc_account_picker_offers_only_this_book_and_what_is_linked(
        client, scene, db_session):
    """Reported from production: the picker listed 123 accounts, ~100 of them the
    FeiHe group's Chinese ones.

    Narrowing the sync's scope could not fix it on its own. nc_bank_accounts is an
    upsert-only mirror by design — an account that stops appearing in vouchers may
    still have reconciliation matches hanging off it — so rows a previous sync
    wrote stay for good, and no later sync takes them back out. Deriving the list
    from journal_voucher_lines is self-correcting instead: an account with no line
    on this book has no ledger side, so a reconciliation against it could never
    balance.
    """
    from app.models.nc_bank_account import NcBankAccount

    # An account this book DOES post to but nobody has linked yet — the case the
    # whole picker exists for. Without this assertion a rule that only ever
    # offered already-linked accounts would pass every check below, and setting up
    # a new account would become impossible.
    used_unlinked = NcBankAccount(nc_pk="NCBANK3", code="4010351",
                                  name="RBC USD", currency="USD")
    # The group's account: in the mirror because an earlier sync wrote it, but no
    # line on this book ever touches it and nobody has linked it. This is the ~100
    # that were cluttering the production picker.
    group_account = NcBankAccount(nc_pk="NCBANK4", code="23050162515100000035",
                                  name="中国建设银行股份有限公司齐齐哈尔分行0035",
                                  currency="CNY")
    db_session.add_all([used_unlinked, group_account])
    await db_session.flush()
    jv = JournalVoucher(jv_number="JV-202607-9001", voucher_word="JV",
                        voucher_date=date(2026, 7, 9), fiscal_period="2026-07",
                        status=POSTED, nc_source_pk="NCPK9001", source_service="nc")
    db_session.add(jv)
    await db_session.flush()
    db_session.add(JournalVoucherLine(
        jv_id=jv.id, line_no=1, account_code="100201", local_debit=D("10"),
        orig_debit=D("10"), currency="CAD", bank_account_id=used_unlinked.id))
    await db_session.flush()

    r = await client.get("/finance/v1/bank-recon/nc-accounts", headers=_h())
    assert r.status_code == 200, r.text
    codes = {a["code"] for a in r.json()["accounts"]}

    assert "4010351" in codes   # ★ posted to by this book, not yet linked -> offered
    assert "1033760" in codes   # linked to a BankAccount -> offered whatever else
    # 1060 IS offered: the scene's inter-bank transfer posts its far leg there, so
    # it has a ledger side like any other.
    assert "1060" in codes
    # ★ the one that must NOT be there
    assert "23050162515100000035" not in codes


# ── the opening balance ───────────────────────────────────────────────────────

async def test_opening_excludes_ncs_opening_voucher_but_keeps_real_postings(db_session):
    """Sign off refused on RBC July 2026 over a difference of −820,087.14 while
    every one of the 260 ledger lines was matched — because the difference was
    not in the period at all. It was the carry-in.

    NC writes an opening voucher (VOUCHERKIND 2) into period 00 to carry a year's
    closing forward. Summing it alongside the transactions it summarises counts
    the same money twice: 245,567.79 (the 2025 one, equal to all of 2024) plus
    574,519.35 (the 2026 one, equal to 2024 + 2025) — exactly the difference.
    """
    from app.models.nc_bank_account import NcBankAccount
    from app.services.bank_book import opening_balance

    db_session.add_all([
        ChartOfAccount(code="1002", name="Cash on Bank", account_type="asset",
                       normal_balance="debit", is_postable=False),
        ChartOfAccount(code="100201", name="Checking", account_type="asset",
                       normal_balance="debit", is_postable=True, parent_code="1002"),
    ])
    nc = NcBankAccount(nc_pk="NCOPEN1", code="9999", name="Opening test", currency="CAD")
    db_session.add(nc)
    await db_session.flush()

    async def line(num, day, dr, cr, kind):
        jv = JournalVoucher(jv_number=f"JV-202601-{num:04d}", voucher_word="JV",
                            voucher_date=date(2026, 1, day), fiscal_period="2026-01",
                            status=POSTED, nc_source_pk=f"OPK{num}", source_service="nc",
                            nc_voucher_kind=kind)
        db_session.add(jv)
        await db_session.flush()
        db_session.add(JournalVoucherLine(
            jv_id=jv.id, line_no=1, account_code="100201",
            orig_debit=D(dr), orig_credit=D(cr), local_debit=D(dr), local_credit=D(cr),
            currency="CAD", bank_account_id=nc.id))
        await db_session.flush()

    await line(1, 5, "100.00", "0", 0)     # ordinary movement
    await line(2, 6, "0", "30.00", 0)      # ordinary movement
    await line(3, 7, "7.00", "0", 4)       # ★ R&D carry-forward — a real posting
    await line(4, 8, "70.00", "0", 2)      # ★ NC's opening voucher — a restatement

    got = await opening_balance(db_session, nc.id, date(2026, 2, 1))

    # 100 − 30 + 7 = 77. The kind-2 restatement of 70 must NOT be added on top;
    # kinds other than 2 must still count, or the number breaks the other way.
    assert got == D("77.00")
