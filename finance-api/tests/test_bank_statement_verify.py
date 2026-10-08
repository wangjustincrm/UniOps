"""The statement verifier — the gate that makes a vision read safe to post.

The fixture is the real RBC July 2026 shape with the amounts kept (they are the
published statement totals the QuickBooks report also shows) and the vendor
descriptions generalised. It reproduces the two things that make this statement
hard:

  - a running balance printed only on SOME lines (the last of each date group,
    plus one mid-group after a transfer credit);
  - `BR TO BR`, which text extraction places among the debits and which is
    actually a CREDIT.

Every test here is about one question: can a wrong parse get through? A verifier
that only checked opening + Σ == closing would pass a statement with two
compensating sign errors, so the printed per-side counts and totals and the
per-line balances are checked independently.
"""
from datetime import date
from decimal import Decimal

from app.services.bank_statement_parse import (
    ParsedStatement, StatementLine, payload_to_statement, verify,
)

D = Decimal
JUL = date(2026, 7, 1)


def _line(seq, day, desc, amount, balance=None):
    return StatementLine(seq=seq, txn_date=date(2026, 7, day), description=desc,
                         amount=D(amount), running_balance=D(balance) if balance else None)


def _statement(lines, **over):
    kw = dict(
        period_start=date(2026, 6, 30), period_end=date(2026, 7, 31),
        opening_balance=D("564623.34"), closing_balance=D("709868.72"),
        lines=lines,
        printed_total_debits=D("1269496.36"), printed_total_credits=D("1414741.74"),
        printed_debit_count=30, printed_credit_count=7,
    )
    kw.update(over)
    return ParsedStatement(**kw)


def _july():
    """A small statement that still ties: 3 debits, 2 credits, real anchors."""
    lines = [
        _line(1, 2, "Bill payment - 8642 PROPANE CO", "-31.64"),
        _line(2, 2, "Direct Deposits (PDS) service total", "-48336.03"),
        _line(3, 2, "Misc Payment PAY-FILE FEES", "-2.00", "516253.67"),
        _line(4, 3, "Deposit Int", "32.62", "516286.29"),
        # The one that text order gets wrong: printed among the debits, is a credit.
        _line(5, 31, "BR TO BR - 2402", "14572.68", "530858.97"),
    ]
    return _statement(
        lines,
        closing_balance=D("530858.97"),
        printed_total_debits=D("48369.67"), printed_total_credits=D("14605.30"),
        printed_debit_count=3, printed_credit_count=2,
    )


# ── the happy path, and its per-side arithmetic ────────────────────────────────

def test_a_correct_statement_verifies():
    st = _july()
    ok, errors = verify(st)
    assert ok is True, errors
    assert errors == []
    assert st.verified is True
    assert st.total_debits == D("48369.67")
    assert st.total_credits == D("14605.30")
    assert len(st.debits) == 3 and len(st.credits) == 2


def test_the_real_july_anchors_are_internally_consistent():
    """Guards the fixture itself: 564,623.34 − 1,269,496.36 + 1,414,741.74 =
    709,868.72, which is what the RBC statement and the QuickBooks report both
    print. If this ever fails, the numbers in these tests drifted, not the code."""
    assert (D("564623.34") - D("1269496.36") + D("1414741.74")) == D("709868.72")


# ── what must NOT get through ─────────────────────────────────────────────────

def test_a_flipped_sign_is_caught_and_the_line_is_named():
    """The failure this whole module exists for: BR TO BR read as a debit."""
    st = _july()
    st.lines[4].amount = D("-14572.68")
    ok, errors = verify(st)
    assert ok is False
    joined = " ".join(errors)
    assert "Line 5" in joined and "BR TO BR" in joined
    assert "credit lines but the statement prints" in joined


def test_two_compensating_sign_errors_do_not_cancel_out():
    """opening + Σ == closing alone would pass this, and so would the per-side
    counts AND totals AND the trailing balance — the swap preserves all of them.
    This is the verifier's one genuine blind spot, so it is refused explicitly
    rather than accepted on a coin flip."""
    st = _statement(
        [_line(1, 2, "Payment out", "100.00"),
         _line(2, 2, "Deposit in", "-100.00", "564623.34")],
        closing_balance=D("564623.34"),
        printed_total_debits=D("100.00"), printed_total_credits=D("100.00"),
        printed_debit_count=1, printed_credit_count=1,
    )
    ok, errors = verify(st)
    # the end-to-end identity is satisfied...
    assert st.opening_balance + sum(ln.amount for ln in st.lines) == st.closing_balance
    # ...and it is still rejected, on the per-line balance.
    assert ok is False
    assert any("cannot tell which is the payment" in e for e in errors)
    assert any("Lines 1 and 2" in e for e in errors)


def test_a_dropped_line_is_caught():
    st = _july()
    del st.lines[1]                      # lose the 48,336.03 batch
    for i, ln in enumerate(st.lines, start=1):
        ln.seq = i
    ok, errors = verify(st)
    assert ok is False
    assert any("48336.03" in e for e in errors)      # named in the off-by
    assert any("debit lines but the statement prints" in e for e in errors)


def test_an_invented_line_is_caught():
    st = _july()
    st.lines.append(_line(6, 15, "Phantom fee", "-5.00"))
    ok, errors = verify(st)
    assert ok is False
    assert any("off by -5.00" in e for e in errors)


def test_a_balance_row_read_as_a_transaction_is_caught():
    st = _july()
    st.lines.insert(3, _line(99, 2, "Opening balance", "0.00"))
    ok, errors = verify(st)
    assert ok is False
    assert any("zero amount" in e for e in errors)


def test_a_line_outside_the_period_is_caught():
    st = _july()
    st.lines[0].txn_date = date(2026, 8, 3)
    ok, errors = verify(st)
    assert ok is False
    assert any("outside the statement period" in e for e in errors)


def test_an_empty_description_is_caught():
    st = _july()
    st.lines[0].description = "   "
    ok, errors = verify(st)
    assert ok is False
    assert any("no description" in e for e in errors)


def test_no_lines_at_all_is_not_a_pass():
    ok, errors = verify(_statement([]))
    assert ok is False and errors


def test_reversed_period_is_caught():
    st = _july()
    st.period_start, st.period_end = st.period_end, st.period_start
    ok, errors = verify(st)
    assert ok is False
    assert any("before it starts" in e for e in errors)


# ── statements that print fewer anchors ───────────────────────────────────────

def test_a_statement_without_printed_totals_still_checks_what_it_can():
    """Not every bank prints per-side totals and counts. The identity and the
    per-line balances still apply — the gate gets weaker, never absent."""
    st = _july()
    st.printed_total_debits = st.printed_total_credits = None
    st.printed_debit_count = st.printed_credit_count = None
    assert verify(st)[0] is True

    st.lines[4].amount = D("-14572.68")     # the sign error again
    ok, errors = verify(st)
    assert ok is False
    assert any("Line 5" in e for e in errors)       # caught by the balance anchor alone


def test_without_any_anchors_a_sign_error_can_still_be_caught_by_the_closing():
    """The weakest case a real bank could give us: no totals, no counts, no
    running balances. Documented deliberately — the closing balance is then the
    only check, and it catches this one."""
    st = _july()
    st.printed_total_debits = st.printed_total_credits = None
    st.printed_debit_count = st.printed_credit_count = None
    for ln in st.lines:
        ln.running_balance = None
    assert verify(st)[0] is True
    st.lines[4].amount = D("-14572.68")
    ok, errors = verify(st)
    assert ok is False
    assert any("off by" in e for e in errors)


# ── payload -> statement ──────────────────────────────────────────────────────

def _payload(**over):
    p = {
        "account_no": "02705 103-376-0", "currency": "CAD",
        "period_start": "2026-06-30", "period_end": "2026-07-31",
        "opening_balance": 564623.34, "closing_balance": 564591.70,
        "printed_total_debits": 31.64, "printed_total_credits": 0,
        "printed_debit_count": 1, "printed_credit_count": 0,
        "lines": [{"date": "2026-07-02", "description": "Bill payment - 8642",
                   "amount": -31.64, "running_balance": 564591.70}],
    }
    p.update(over)
    return p


def test_payload_becomes_a_verified_statement():
    st = payload_to_statement(_payload())
    assert st.verified is True, st.verify_errors
    assert st.account_no == "02705 103-376-0"
    assert st.lines[0].amount == D("-31.64")
    assert st.lines[0].running_balance == D("564591.70")
    assert st.lines[0].seq == 1


def test_payload_floats_are_quantized_to_cents():
    """JSON gives floats; money must not carry binary noise into a comparison
    against a printed figure."""
    st = payload_to_statement(_payload(
        opening_balance=100.10, closing_balance=100.30,
        printed_total_debits=0, printed_total_credits=0.20,
        printed_debit_count=0, printed_credit_count=1,
        lines=[{"date": "2026-07-02", "description": "Interest", "amount": 0.20,
                "running_balance": 100.30}]))
    assert st.lines[0].amount == D("0.20")
    assert st.verified is True, st.verify_errors


def test_a_malformed_payload_is_refused_by_field_name():
    from app.services.bank_statement_parse import StatementUnparseable
    import pytest
    for bad, needle in (
        ({"lines": [{"date": "2026-07-02", "description": "x"}]}, "amount"),
        ({"lines": [{"date": "nope", "description": "x", "amount": 1}]}, "date"),
        ({"opening_balance": None}, "opening_balance"),
        ({"lines": "not a list"}, "not a list"),
    ):
        with pytest.raises(StatementUnparseable, match=needle):
            payload_to_statement(_payload(**bad))


# ── the sign solver ───────────────────────────────────────────────────────────
#
# Direction is the one field a statement encodes only by column position, which
# text extraction loses and a model therefore guesses. Measured on the real RBC
# July statement: one whole-document Haiku read returned every date, description,
# magnitude and running balance correctly and inverted the signs wholesale (all 18
# "Direct Deposits (PDS) service total" batches as credits, "BR TO BR" as a
# debit). The balances pin the signs down, so they are solved, not trusted.

def _unsigned(st):
    """What extraction hands over on a bad day: right numbers, wrong directions."""
    for ln in st.lines:
        ln.amount = abs(ln.amount)
    return st


def test_solver_recovers_every_sign_from_the_balances():
    from app.services.bank_statement_parse import solve_signs
    st = _unsigned(_july())
    notes = solve_signs(st)
    assert notes == []
    assert [ln.amount for ln in st.lines] == [
        D("-31.64"), D("-48336.03"), D("-2.00"), D("32.62"), D("14572.68")]
    assert verify(st)[0] is True


def test_solver_fixes_a_wholesale_inversion():
    """The exact failure observed: every sign flipped."""
    from app.services.bank_statement_parse import solve_signs
    st = _july()
    for ln in st.lines:
        ln.amount = -ln.amount
    solve_signs(st)
    assert verify(st)[0] is True
    assert st.lines[4].amount == D("14572.68")      # BR TO BR back to a credit


def test_solver_reports_a_misread_amount_instead_of_forcing_a_fit():
    """No sign assignment reaches the printed balance — so a MAGNITUDE is wrong,
    and the solver must say that rather than pick the closest combination."""
    from app.services.bank_statement_parse import solve_signs
    st = _july()
    st.lines[1].amount = D("-48336.30")            # transposed digits
    notes = solve_signs(st)
    assert any("no combination" in n and "misread" in n for n in notes)
    assert st.lines[1].amount == D("-48336.30")     # untouched, not fudged
    assert verify(st)[0] is False


def test_solver_reports_an_undetermined_segment_rather_than_choosing():
    from app.services.bank_statement_parse import solve_signs
    st = _statement(
        [_line(1, 2, "Payment out", "100.00"),
         _line(2, 2, "Deposit in", "100.00", "564623.34")],
        closing_balance=D("564623.34"),
        printed_total_debits=None, printed_total_credits=None,
        printed_debit_count=None, printed_credit_count=None,
    )
    notes = solve_signs(st)
    assert any("different direction combinations" in n for n in notes)
    assert verify(st)[0] is False


def test_solver_uses_the_closing_balance_for_the_trailing_run():
    """The last group often prints no balance of its own. This is the segment that
    was wrong on the real statement, so it gets its own test."""
    from app.services.bank_statement_parse import solve_signs
    lines = [
        _line(1, 30, "Funds transfer credit", "500000.00", "835321.57"),
        _line(2, 31, "BR TO BR - 2402", "14572.68"),
        _line(3, 31, "Direct Deposits (PDS) service total", "28918.08"),
        _line(4, 31, "Direct Deposits (PDS) service total", "108498.24"),
        _line(5, 31, "Cheque - 23", "2609.21"),
    ]
    st = _statement(lines, opening_balance=D("335321.57"), closing_balance=D("709868.72"),
                    printed_total_debits=D("140025.53"), printed_total_credits=D("514572.68"),
                    printed_debit_count=3, printed_credit_count=2)
    assert solve_signs(st) == []
    assert [ln.amount for ln in st.lines] == [
        D("500000.00"), D("14572.68"), D("-28918.08"), D("-108498.24"), D("-2609.21")]
    assert verify(st)[0] is True


def test_solver_refuses_a_segment_too_wide_to_enumerate():
    """A statement that prints no interim balances at all leaves one huge segment.
    Bounded rather than hung, and it says the signs were left alone."""
    from app.services.bank_statement_parse import solve_signs
    lines = [_line(i, 2, f"txn {i}", "10.00") for i in range(1, 20)]
    st = _statement(lines, opening_balance=D("0.00"), closing_balance=D("190.00"),
                    printed_total_debits=None, printed_total_credits=None,
                    printed_debit_count=None, printed_credit_count=None)
    notes = solve_signs(st)
    assert any("too many to determine" in n for n in notes)
    assert verify(st)[0] is False


# ── which page the closing balance comes from ─────────────────────────────────

import pytest  # noqa: E402

def _fake_pages(monkeypatch, replies):
    """Stand in for the PDF split and the per-page AI reads."""
    from app.services import bank_statement_parse as bsp
    monkeypatch.setattr(bsp, "_single_pages", lambda pdf: [b"p"] * len(replies))
    it = iter(replies)
    monkeypatch.setattr(bsp, "_ask", lambda content, what: next(it))
    return bsp


def _page1(closing, lines):
    return {"account_no": "100301000001994", "currency": "CNY",
            "period_start": "2026-07-01", "period_end": "2026-07-31",
            "opening_balance": 34411.68, "closing_balance": closing, "lines": lines}


BOC_P1 = [{"date": "2026-07-23", "description": "Transfer ZHENGZHOU FANCHUANG",
           "amount": -20200.00, "running_balance": 14211.68},
          {"date": "2026-07-29", "description": "Transfer Canada Royal Milk ULC",
           "amount": 23593.00, "running_balance": 37804.68}]
BOC_P2 = [{"date": "2026-07-30", "description": "Transfer Shanghai Ehsure",
           "amount": -7744.00, "running_balance": 30060.68},
          {"date": "2026-07-30", "description": "Transfer Unitrans (Beijing)",
           "amount": -9209.00, "running_balance": 20851.68}]


def test_the_closing_balance_printed_on_the_last_page_wins(monkeypatch):
    """BOC CNY July 2026: page 1 ends in "Balance Carried Forward To Next Page
    37,804.68" and the real "Closing Balance 20,851.68" is on page 2. Reading the
    summary from page 1 only stored 37,804.68 and an unverified statement."""
    # even when page 1 wrongly reports the carry-forward as a closing
    bsp = _fake_pages(monkeypatch, [_page1(37804.68, BOC_P1),
                                    {"closing_balance": 20851.68, "lines": BOC_P2}])
    payload = bsp.extract_payload(b"%PDF", "BOC CNY 1994 2026.7.pdf")
    assert payload["closing_balance"] == 20851.68
    assert payload["_closing_from"] == "page 2"

    st = bsp.payload_to_statement(payload)
    assert st.closing_balance == Decimal("20851.68")
    assert st.verified


def test_a_closing_balance_on_page_one_still_counts(monkeypatch):
    """RBC prints its account summary, closing included, on page 1 only."""
    bsp = _fake_pages(monkeypatch, [_page1(20851.68, BOC_P1),
                                    {"closing_balance": None, "lines": BOC_P2}])
    payload = bsp.extract_payload(b"%PDF", "rbc.pdf")
    assert payload["closing_balance"] == 20851.68
    assert payload["_closing_from"] == "page 1"


def test_no_printed_closing_falls_back_to_the_last_printed_balance(monkeypatch):
    bsp = _fake_pages(monkeypatch, [_page1(None, BOC_P1),
                                    {"closing_balance": None, "lines": BOC_P2}])
    payload = bsp.extract_payload(b"%PDF", "x.pdf")
    assert payload["closing_balance"] == 20851.68
    assert payload["_closing_from"] == "last printed running balance"


def test_no_closing_anywhere_is_refused_not_guessed(monkeypatch):
    last = dict(BOC_P2[-1], running_balance=None)
    bsp = _fake_pages(monkeypatch, [_page1(None, BOC_P1),
                                    {"closing_balance": None, "lines": [BOC_P2[0], last]}])
    with pytest.raises(bsp.StatementUnparseable):
        bsp.extract_payload(b"%PDF", "x.pdf")


# ── one statement, several currencies (ICBC) ──────────────────────────────────

ICBC_P1 = {"account_no": "0001230719200018518", "currency": "USD",
           "period_start": "2026-07-01", "period_end": "2026-07-31",
           # what a reader with no currency to aim at did: USD opening, USD row
           "opening_balance": 4935.38, "closing_balance": 4929.38,
           "lines": [{"date": "2026-07-01", "description": "CHG Monthly Adm Fee - Corporate",
                      "amount": -6.00, "running_balance": 4929.38, "currency": "USD"},
                     {"date": "2026-07-01", "description": "CHG Monthly Adm Fee - Corporate",
                      "amount": -5.00, "running_balance": 9580.88, "currency": "CAD"}]}
ICBC_P1_CAD = dict(ICBC_P1, opening_balance=9585.88, closing_balance=None,
                   lines=[ICBC_P1["lines"][1]])
ICBC_P2_CAD = {"opening_balance": None, "closing_balance": 9580.88, "lines": []}


def test_the_account_currency_reaches_every_page_prompt(monkeypatch):
    from app.services import bank_statement_parse as bsp
    monkeypatch.setattr(bsp, "_single_pages", lambda pdf: [b"p", b"p"])
    seen = []

    def ask(content, what):
        seen.append(content[-1]["text"])
        return ICBC_P1_CAD if len(seen) == 1 else ICBC_P2_CAD
    monkeypatch.setattr(bsp, "_ask", ask)
    bsp.extract_payload(b"%PDF", "ICBC 2026.7.pdf", currency="cad")
    assert all("THIS ACCOUNT IS IN CAD" in t for t in seen) and len(seen) == 2


def test_an_icbc_statement_is_read_for_the_accounts_currency_only(monkeypatch):
    """ICBC prints CNY, USD and CAD sub-accounts in one statement. Read for the
    CAD account without saying so, it came back as the USD opening (4,935.38),
    the USD fee (−6.00) and — from page 2 — the CAD closing (9,580.88)."""
    bsp = _fake_pages(monkeypatch, [ICBC_P1_CAD, ICBC_P2_CAD])
    payload = bsp.extract_payload(b"%PDF", "ICBC 2026.7.pdf", currency="CAD")
    st = bsp.payload_to_statement(payload)
    assert st.currency == "CAD"
    assert (st.opening_balance, st.closing_balance) == (Decimal("9585.88"), Decimal("9580.88"))
    assert [ln.amount for ln in st.lines] == [Decimal("-5.00")]
    assert st.verified


def test_a_row_in_another_currency_is_dropped_even_if_the_reader_returns_it(monkeypatch):
    bsp = _fake_pages(monkeypatch, [dict(ICBC_P1_CAD, lines=ICBC_P1["lines"]), ICBC_P2_CAD])
    payload = bsp.extract_payload(b"%PDF", "ICBC 2026.7.pdf", currency="CAD")
    assert [ln["amount"] for ln in payload["lines"]] == [-5.00]
    assert payload["_other_currency_lines_dropped"] == 1


def test_the_opening_can_come_from_a_later_page(monkeypatch):
    """The account's section may begin on page 2 of a combined statement."""
    bsp = _fake_pages(monkeypatch, [
        dict(ICBC_P1_CAD, opening_balance=None, lines=[]),
        {"opening_balance": 9585.88, "closing_balance": 9580.88,
         "lines": [ICBC_P1["lines"][1]]}])
    payload = bsp.extract_payload(b"%PDF", "ICBC 2026.7.pdf", currency="CAD")
    assert payload["opening_balance"] == 9585.88 and payload["_opening_from"] == "page 2"


def test_no_opening_for_the_currency_is_refused(monkeypatch):
    bsp = _fake_pages(monkeypatch, [dict(ICBC_P1_CAD, opening_balance=None), ICBC_P2_CAD])
    with pytest.raises(bsp.StatementUnparseable):
        bsp.extract_payload(b"%PDF", "ICBC 2026.7.pdf", currency="CAD")


def test_a_page_with_only_other_currencies_does_not_supply_the_closing(monkeypatch):
    """ICBC July, read for the USD account. Page 2 prints only the CAD closing
    (9,580.88); told to return null when there is no USD closing, Haiku returned
    the CAD one anyway — and last-page-wins made it the USD closing. The pick is
    now made in code from sections the reader labels with their currency."""
    usd_row = ICBC_P1["lines"][0]
    p1 = dict(ICBC_P1, lines=[usd_row], balance_sections=[
        {"currency": "USD", "opening_balance": 4935.38, "closing_balance": 4929.38},
        {"currency": "CAD", "opening_balance": 9585.88, "closing_balance": None}])
    p2 = {"opening_balance": None, "closing_balance": 9580.88,   # the misread
          "balance_sections": [{"currency": "CAD", "opening_balance": None,
                                "closing_balance": 9580.88}],
          "lines": []}
    bsp = _fake_pages(monkeypatch, [p1, p2])
    st = bsp.payload_to_statement(
        bsp.extract_payload(b"%PDF", "ICBC 2026.7.pdf", currency="USD"))
    assert (st.opening_balance, st.closing_balance) == (Decimal("4935.38"), Decimal("4929.38"))
    assert st.raw_payload["_closing_from"] == "page 1"
    assert st.verified

    # and the same PDF for the CAD account takes the CAD figures from both pages
    bsp = _fake_pages(monkeypatch, [dict(p1, lines=[ICBC_P1["lines"][1]]), p2])
    st = bsp.payload_to_statement(
        bsp.extract_payload(b"%PDF", "ICBC 2026.7.pdf", currency="CAD"))
    assert (st.opening_balance, st.closing_balance) == (Decimal("9585.88"), Decimal("9580.88"))


def test_a_total_offered_as_the_closing_loses_to_the_last_printed_balance(monkeypatch):
    """JPM Toronto prints no closing balance, only the last row's balance and
    then "Total Dr / Total Cr / Net Movement". Haiku offered Net Movement
    (41,810.24) as the closing of a statement that closes at 125,350.01."""
    p1 = {"account_no": None, "currency": None, "period_start": None, "period_end": None,
          "opening_balance": None, "closing_balance": None, "lines": []}   # cover page
    p2 = {"period_start": "2026-07-01", "period_end": "2026-07-31",
          "account_no": "4011812940", "currency": "CAD",
          "opening_balance": 83539.77, "closing_balance": 41810.24,        # Net Movement
          "lines": [{"date": "2026-07-02", "description": "ACH Credit Received",
                     "amount": 41810.24, "running_balance": 125350.01}]}
    bsp = _fake_pages(monkeypatch, [p1, p2])
    payload = bsp.extract_payload(b"%PDF", "jpm.pdf", currency="CAD")
    assert payload["closing_balance"] == 125350.01
    assert payload["_closing_from"] == "last printed running balance"
    assert payload["_closing_rejected"] == ["page 2: 41810.24"]
    # the header facts came from page 2, not the cover page
    assert (payload["period_start"], payload["account_no"]) == ("2026-07-01", "4011812940")
    assert bsp.payload_to_statement(payload).verified


def test_directions_are_proven_by_printed_totals_when_no_balance_is_printed():
    """JPM US prints deposits and withdrawals in separate sections and no running
    balance at all; the per-side counts and totals decide every direction."""
    from app.services.bank_statement_parse import (ParsedStatement, StatementLine,
                                                    payload_to_statement)
    lines = [("2026-07-02", "Chips Credit", 200000.00), ("2026-07-03", "Wire out", -150000.00),
             ("2026-07-09", "ACH debit", -30.42), ("2026-07-20", "Deposit", 70.42)]
    payload = {"period_start": "2026-07-01", "period_end": "2026-07-31",
               "opening_balance": 1000.00, "closing_balance": 51040.00,
               "printed_total_credits": 200070.42, "printed_credit_count": 2,
               "printed_total_debits": 150030.42, "printed_debit_count": 2,
               # every sign read backwards
               "lines": [{"date": d, "description": t, "amount": -a, "running_balance": None}
                         for d, t, a in lines]}
    st = payload_to_statement(payload)
    assert [ln.amount for ln in st.lines] == [Decimal(str(a)).quantize(Decimal("0.01"))
                                              for _d, _t, a in lines]
    assert st.verified, st.verify_errors


def _jpm_us(credit_first_pair: bool):
    from app.services.bank_statement_parse import payload_to_statement
    ret, pay = (48076.00, -48076.00) if credit_first_pair else (-48076.00, 48076.00)
    return payload_to_statement({
        "period_start": "2026-07-01", "period_end": "2026-07-31",
        "opening_balance": 1000.00, "closing_balance": 201000.00,
        "printed_total_credits": 248076.00, "printed_credit_count": 2,
        "printed_total_debits": 48076.00, "printed_debit_count": 1,
        "lines": [
            {"date": "2026-07-13", "description": "EFT Return Items Offset", "amount": ret,
             "running_balance": None},
            {"date": "2026-07-16", "description": "Chips Credit", "amount": 200000.00,
             "running_balance": None},
            {"date": "2026-07-16", "description": "Corp Pay", "amount": pay,
             "running_balance": None}]})


def test_a_same_amount_pair_under_printed_headings_is_not_ambiguous():
    """JPM US July: 48,076.00 returned (Deposits and Additions, 07/13) and
    48,076.00 paid (Electronic Withdrawals, 07/16). The headings say which is
    which and the reading ties to both printed totals — nothing to confirm."""
    st = _jpm_us(credit_first_pair=True)
    assert st.direction_basis == "printed sections"
    assert st.verified, st.verify_errors


def test_the_pair_is_still_flagged_when_the_reading_disagrees_with_the_totals():
    """If the reader's sections do not tie to the printed totals, its placement
    is not evidence, and an equal-magnitude pair is genuinely undetermined."""
    from app.services.bank_statement_parse import payload_to_statement
    st = payload_to_statement({
        "period_start": "2026-07-01", "period_end": "2026-07-31",
        "opening_balance": 1000.00, "closing_balance": 201000.00,
        "printed_total_credits": 248076.00, "printed_credit_count": 2,
        "printed_total_debits": 48076.00, "printed_debit_count": 1,
        "lines": [   # the Chips credit read as a withdrawal: totals no longer tie
            {"date": "2026-07-13", "description": "EFT Return", "amount": 48076.00,
             "running_balance": None},
            {"date": "2026-07-16", "description": "Chips Credit", "amount": -200000.00,
             "running_balance": None},
            {"date": "2026-07-16", "description": "Corp Pay", "amount": -48076.00,
             "running_balance": None}]})
    assert st.direction_basis is None
    assert not st.verified
    assert any("48076.00 in opposite directions" in e for e in st.verify_errors)
