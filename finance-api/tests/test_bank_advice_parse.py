"""Payment-advice parsing.

Fixtures are synthetic but reproduce the real layout-mode output exactly, quirks
included — the wide column padding, the truncated name that overflows onto its own
line, the customer-number column that sometimes merges with the name. Real vendor
names and amounts stay out of the repo; `scripts/validate_bank_docs.py` is what
runs the parser against actual files.

Measured against the real July set while writing this: 19/19 files tie, 237 vendor
lines, 1,031,087.99 total.
"""
from decimal import Decimal

import pytest

from app.services.bank_advice_parse import (
    BILL_PAYMENT, PDS_BATCH, AdviceUnparseable, detect_kind, parse_text,
    split_bill_payments,
)


def _pad(*cells: str) -> str:
    """Build one layout-mode row. pypdf pads columns out to the PDF's user-space
    width; only the fact that gaps are 2+ spaces matters to the parser."""
    return "".join(c.ljust(40) for c in cells)


PDS_HEADER = """\
Payment File Content
                                        Mengqi Zhang, ACME CO
                                        Report Creation Date : Jul  02,  2026 03:36:25 PM ET

Client Number :                         9804420000 - PDS CAD - ACME CO          Destination Country :        Canada
Amount Range :                          From        6.78        To      12,874.90
Payment Group(s) :                      Vendors                                 Status :        All
Environment :                           Live

______________________________________________________________________

Status : Valid
Customer Number                         Customer Name                           Destination Currency          Payment Number          Amount
"""


def _pds(rows: str, count: int, total: str) -> str:
    return (PDS_HEADER + rows
            + f"\nNumber Of Payments :   {count} Total:            {total}\n    1\n")


THREE_ROWS = (
    _pad("ALPHA SUPPLY", "Alpha Supply", "CAD", "00", "340.75") + "\n"
    + _pad("BETA-STAINLESS-STEEL", "Beta-Stainless-Steels-Inc", "CAD", "01", "11,997.21") + "\n"
    + _pad("GAMMA FREIGHT", "Gamma Freight Canada", "CAD", "00", "765.74") + "\n"
)


# ── kind detection ─────────────────────────────────────────────────────────────

def test_detects_the_two_kinds():
    assert detect_kind(PDS_HEADER) == PDS_BATCH
    assert detect_kind("Released Bill Payment Confirmation Numbers :\n") == BILL_PAYMENT
    assert detect_kind("Some other bank report\n") is None


def test_an_unknown_document_is_refused_by_name():
    with pytest.raises(AdviceUnparseable, match="Payment File Content"):
        parse_text("Quarterly Statement of Something Else\n")


# ── PDS batch ──────────────────────────────────────────────────────────────────

def test_pds_batch_ties_and_splits_the_payee_columns():
    adv = parse_text(_pds(THREE_ROWS, 3, "13,103.70"))
    assert adv.kind == PDS_BATCH
    assert adv.tie_ok is True and adv.tie_error is None
    assert adv.advice_date.isoformat() == "2026-07-02"
    assert adv.client_number == "9804420000"     # statement shows "GRADS9804420000"
    assert adv.currency == "CAD"
    assert adv.total == Decimal("13103.70")
    assert [ln.amount for ln in adv.lines] == [
        Decimal("340.75"), Decimal("11997.21"), Decimal("765.74")]
    assert adv.lines[1].payee_code == "BETA-STAINLESS-STEEL"
    assert adv.lines[1].payee_name == "Beta-Stainless-Steels-Inc"
    assert [ln.seq for ln in adv.lines] == [1, 2, 3]


def test_a_wrapped_vendor_name_rejoins_its_row():
    """The name column is truncated, so "Gamma Freight Canada Corpora(tion)"
    arrives as a row plus an orphan line. It must extend the previous name and
    must NOT become a fourth vendor."""
    rows = THREE_ROWS + "                                        Corpora\n"
    adv = parse_text(_pds(rows, 3, "13,103.70"))
    assert len(adv.lines) == 3
    assert adv.lines[-1].payee_name == "Gamma Freight Canada Corpora"
    assert adv.tie_ok is True


def test_a_merged_payee_column_keeps_the_whole_string_as_the_name():
    """When the extractor runs the number and name into one column there is no
    delimiter to split on, and inventing one would corrupt the name. Amounts are
    unaffected — they come from their own column."""
    rows = _pad("DELTA HOLDINGS DELTA HOLDINGS", "CAD", "00", "50.00") + "\n"
    adv = parse_text(_pds(rows, 1, "50.00"))
    assert adv.lines[0].payee_code is None
    assert adv.lines[0].payee_name == "DELTA HOLDINGS DELTA HOLDINGS"
    assert adv.lines[0].amount == Decimal("50.00")
    assert adv.tie_ok is True


def test_a_short_read_fails_the_tie_with_both_numbers_named():
    """The whole point of the tie test: a parser that silently drops a row would
    otherwise hand the matcher a batch that is quietly light."""
    adv = parse_text(_pds(THREE_ROWS, 4, "13,203.70"))
    assert adv.tie_ok is False
    assert "13103.70" in adv.tie_error and "13203.70" in adv.tie_error
    assert "read 3 vendor rows" in adv.tie_error and "prints 4" in adv.tie_error


def test_a_missing_footer_is_a_tie_failure_not_a_pass():
    """No printed total means nothing to check against — which must read as "not
    verified", never as "verified"."""
    adv = parse_text(PDS_HEADER + THREE_ROWS)
    assert adv.tie_ok is False
    assert "prints no" in adv.tie_error
    assert len(adv.lines) == 3          # still parsed, just not trusted


def test_chrome_never_becomes_a_vendor_row():
    adv = parse_text(_pds(THREE_ROWS, 3, "13,103.70"))
    names = [ln.payee_name for ln in adv.lines]
    assert not any("Customer" in n or "Environment" in n or "Number Of" in n for n in names)


def test_a_batch_with_no_rows_is_refused():
    with pytest.raises(AdviceUnparseable, match="no vendor rows"):
        parse_text(_pds("", 0, "0.00"))


def test_undated_batch_is_refused():
    """Every advice has to be datable — the matcher's date window is the only thing
    keeping a July batch from clearing an August statement line."""
    text = _pds(THREE_ROWS, 3, "13,103.70").replace("Report Creation Date", "Created")
    with pytest.raises(AdviceUnparseable, match="Report Creation Date"):
        parse_text(text)


# ── bill payments ──────────────────────────────────────────────────────────────

BILL_HEADER = """\
Released Bill Payment Confirmation Numbers :
                                        Mengqi Zhang, ACME CO
                                        Report Creation Date: Jul  02,  2026 04:09:12 PM ET
Payment Confirmation
Corporate Creditor  Amount Payment Date Number Status
"""


def test_bill_payment_keeps_the_confirmation_number():
    """That number is an exact join key — the statement reads
    "Bill payment - 8642 TYENDINAGA PROP"."""
    text = BILL_HEADER + "1 ZETA PROPANE-153579 31.64 Jul 02, 2026 8642 Completed\n*** End of report ***\n"
    adv = parse_text(text)
    assert adv.kind == BILL_PAYMENT
    assert adv.tie_ok is True
    assert adv.confirmation_number == "8642"
    assert adv.lines[0].amount == Decimal("31.64")
    assert adv.lines[0].payee_name == "ZETA PROPANE"
    assert adv.lines[0].payee_code == "153579"
    assert adv.advice_date.isoformat() == "2026-07-02"


def test_bill_payment_reads_the_split_header_variant():
    """The real files print this header two different ways — one run-together
    line, and one with every word on its own line."""
    text = """\
Released Bill Payment Confirmation Numbers :
                                        Report Creation Date: Jul 13, 2026 03:09:05 PM ET
Corporate Creditor Payment
Amount
Payment Date
Confirmation
Number
Status
1 ETA UTILITIES-420044 144.47 Jul 13, 2026 5386 Completed
*** End of report ***
1
"""
    adv = parse_text(text)
    assert adv.tie_ok is True
    assert adv.confirmation_number == "5386"
    assert adv.lines[0].amount == Decimal("144.47")


def test_a_multi_row_bill_file_splits_into_one_advice_per_confirmation():
    """The STATEMENT sees one line per confirmation number, so the advice has to
    match that granularity or the matcher's "one advice, one bank line" invariant
    breaks."""
    text = (BILL_HEADER
            + "1 ZETA PROPANE-153579 31.64 Jul 02, 2026 8642 Completed\n"
            + "2 ETA UTILITIES-420044 144.47 Jul 02, 2026 5386 Completed\n")
    adv = parse_text(text)
    assert adv.confirmation_number is None        # ambiguous on the parent
    assert adv.per_row_confirmations == ["8642", "5386"]
    parts = split_bill_payments(adv)
    assert [p.confirmation_number for p in parts] == ["8642", "5386"]
    assert [p.total for p in parts] == [Decimal("31.64"), Decimal("144.47")]
    assert all(len(p.lines) == 1 and p.tie_ok for p in parts)


def test_a_pds_batch_is_never_split():
    """A batch's whole purpose is that the statement sees ONE merged line."""
    adv = parse_text(_pds(THREE_ROWS, 3, "13,103.70"))
    assert split_bill_payments(adv) == [adv]


def test_a_gap_in_the_bill_row_numbers_fails_the_tie():
    text = (BILL_HEADER
            + "1 ZETA PROPANE-153579 31.64 Jul 02, 2026 8642 Completed\n"
            + "3 ETA UTILITIES-420044 144.47 Jul 02, 2026 5386 Completed\n")
    adv = parse_text(text)
    assert adv.tie_ok is False
    assert "expected 1..2" in adv.tie_error


def test_a_bill_file_with_no_rows_is_refused():
    with pytest.raises(AdviceUnparseable, match="no bill-payment rows"):
        parse_text(BILL_HEADER)
