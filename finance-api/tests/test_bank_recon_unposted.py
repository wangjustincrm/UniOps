"""Bank reconciliation counts unposted vouchers by default (user, 2026-10-14).

The basis is stored on the period (`include_unposted`), so every ledger read of
that period — the difference, auto-match, manual matches, the signed snapshot —
uses the same one. "Unposted" is fiscal.status_filter's definition: a draft NC
still holds as a normal voucher. A draft NC has deleted or discarded since
import is never a movement on the account, ticked or not.
"""
import io
import uuid
from datetime import date
from decimal import Decimal

from app.models.bank import UNMATCHED, BankTransaction
from app.models.journal_voucher import DRAFT, JournalVoucher, JournalVoucherLine
from tests.test_bank_recon_v2_api import _h, _open, _with_statement, client, scene  # noqa: F401

D = Decimal


async def _draft(db_session, scene, num, day, amount, *, state="normal", month=7):
    """An un-tallied NC voucher: a bank fee of `amount` on the scene's account."""
    jv = JournalVoucher(jv_number=f"JV-2026{month:02d}-{num:04d}", voucher_word="JV",
                        voucher_date=date(2026, month, day),
                        fiscal_period=f"2026-{month:02d}", status=DRAFT,
                        nc_voucher_state=state, nc_source_pk=f"NCPKD{num}",
                        source_service="nc")
    db_session.add(jv)
    await db_session.flush()
    db_session.add_all([
        JournalVoucherLine(jv_id=jv.id, line_no=1, account_code="100201",
                           summary=f"Unposted fee {num}", orig_debit=D("0"),
                           orig_credit=D(amount), local_debit=D("0"),
                           local_credit=D(amount), currency="CAD",
                           bank_account_id=scene["nc"].id),
        JournalVoucherLine(jv_id=jv.id, line_no=2, account_code="6603",
                           summary=f"Unposted fee {num}", orig_debit=D(amount),
                           orig_credit=D("0"), local_debit=D(amount),
                           local_credit=D("0"), currency="CAD"),
    ])
    await db_session.flush()
    return jv


async def _period(client, rid):
    r = await client.get(f"/finance/v1/bank-recon/reconciliations/{rid}", headers=_h())
    assert r.status_code == 200, r.text
    return r.json()


async def _basis(client, rid, value):
    return await client.put(f"/finance/v1/bank-recon/reconciliations/{rid}/basis",
                            json={"include_unposted": value}, headers=_h())


async def test_a_new_period_includes_live_unposted_but_never_deleted(
        client, scene, db_session):
    await _draft(db_session, scene, 90, 15, "50.00")
    await _draft(db_session, scene, 91, 16, "777.00", state="deleted")
    await _draft(db_session, scene, 92, 17, "888.00", state="discarded")
    rid = await _open(client, scene["account"])

    p = await _period(client, rid)
    assert p["include_unposted"] is True
    by_summary = {b["summary"]: b for b in p["book_lines"]}
    assert by_summary["Unposted fee 90"]["posted"] is False
    assert "Unposted fee 91" not in by_summary, "NC deleted it — not a movement"
    assert "Unposted fee 92" not in by_summary, "NC discarded it — not a movement"
    assert by_summary["Alpha SupplyEFT-$340.75"]["posted"] is True
    # 358,490.98 posted-only (see test_the_whole_period_reconciles_to_zero) − 50
    assert p["summary"]["book_closing"] == "358440.98"


async def test_unticking_reads_posted_only_and_ticking_brings_it_back(
        client, scene, db_session):
    await _draft(db_session, scene, 90, 15, "50.00")
    rid = await _open(client, scene["account"])

    r = await _basis(client, rid, False)
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["book_closing"] == "358490.98"
    p = await _period(client, rid)
    assert p["include_unposted"] is False
    assert all(b["posted"] for b in p["book_lines"])

    r = await _basis(client, rid, True)
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["book_closing"] == "358440.98"

    rows = (await client.get(
        f"/finance/v1/bank-recon/{scene['account'].id}/reconciliations",
        headers=_h())).json()
    assert rows[0]["include_unposted"] is True


async def test_the_opening_balance_follows_the_basis(client, scene, db_session):
    await _draft(db_session, scene, 89, 20, "100.00", month=6)
    base = f"/finance/v1/bank-recon/{scene['account'].id}/book?date_from=2026-07-01&date_to=2026-07-31"
    ticked = (await client.get(base, headers=_h())).json()
    unticked = (await client.get(base + "&include_unposted=false", headers=_h())).json()
    assert ticked["opening"] == "900.00"
    assert unticked["opening"] == "1000.00"


async def test_unticking_is_refused_while_a_match_holds_an_unposted_line(
        client, scene, db_session):
    await _draft(db_session, scene, 90, 15, "50.00")
    txn = BankTransaction(
        bank_account_id=scene["account"].id, txn_date=date(2026, 7, 15),
        description="Service charge", amount=D("-50.00"), currency="CAD",
        status=UNMATCHED, sort_seq=9, import_hash=f"h9-{uuid.uuid4()}")
    db_session.add(txn)
    await db_session.flush()
    rid = await _open(client, scene["account"])
    p = await _period(client, rid)
    draft_line = next(b for b in p["book_lines"] if b["summary"] == "Unposted fee 90")

    m = await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/matches",
                          json={"bank_transaction_ids": [str(txn.id)],
                                "jv_line_ids": [draft_line["jv_line_id"]]},
                          headers=_h())
    assert m.status_code == 200, m.text

    r = await _basis(client, rid, False)
    assert r.status_code == 422
    assert "Unmatch" in r.json()["detail"]
    assert (await _period(client, rid))["include_unposted"] is True

    await client.delete(f"/finance/v1/bank-recon/reconciliations/{rid}/matches/{m.json()['id']}",
                        headers=_h())
    assert (await _basis(client, rid, False)).status_code == 200


async def test_a_signed_off_period_cannot_change_basis_and_its_report_says_which(
        client, scene, db_session):
    await _with_statement(db_session, scene["account"])
    rid = await _open(client, scene["account"])
    await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/auto-match",
                      headers=_h())
    r = await client.post(f"/finance/v1/bank-recon/reconciliations/{rid}/finalize",
                          headers=_h())
    assert r.status_code == 200, r.text

    blocked = await _basis(client, rid, False)
    assert blocked.status_code == 409 and "finalized" in blocked.json()["detail"]

    xlsx = await client.get(f"/finance/v1/bank-recon/reconciliations/{rid}/report?fmt=xlsx",
                            headers=_h())
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(xlsx.content)).active
    text = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
    assert "Register includes unposted vouchers" in text


async def test_a_reader_cannot_change_the_basis(client, scene):
    rid = await _open(client, scene["account"])
    r = await client.put(f"/finance/v1/bank-recon/reconciliations/{rid}/basis",
                         json={"include_unposted": False}, headers=_h(role="ap_clerk"))
    assert r.status_code == 403
    # paired admission: the same period still reads, and still ticked
    assert (await _period(client, rid))["include_unposted"] is True
