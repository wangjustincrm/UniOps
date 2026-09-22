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
