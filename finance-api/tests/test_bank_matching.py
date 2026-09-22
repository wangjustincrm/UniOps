"""The matching ladder.

Every fixture is a shape taken from the real RBC CAD July 2026 set — the amounts
are the published statement and payment-file figures, the vendor names are
generalised. Measured end to end on the real data (AI-read statement, 19 payment
files, NC's own 260 ledger lines): 37/37 statement lines and 260/260 ledger lines
cleared, 237/237 advice lines linked, one finding naming the statement line whose
payment file was not supplied.

The tests below are about the rules that keep that honest, not about the happy
path alone: nothing clears unless the ledger side sums to the bank side exactly,
nothing is claimed twice, and an ambiguous choice is reported rather than made.
"""
from datetime import date
from decimal import Decimal

from app.services import bank_matching as M

D = Decimal


def bank(id_, day, desc, amount):
    return M.BankTxn(id=id_, txn_date=date(2026, 7, day), description=desc, amount=D(amount))


def book(id_, day, summary, amount, kind="ap"):
    return M.BookLineView(id=id_, voucher_date=date(2026, 7, day), summary=summary,
                          amount=D(amount), contra_kind=kind)


def advice(id_, day, lines, kind="pds_batch", conf=None, tie_ok=True):
    al = [M.AdviceLineView(id=f"{id_}:{i}", payee_name=n, amount=D(a))
          for i, (n, a) in enumerate(lines, start=1)]
    return M.AdviceView(id=id_, kind=kind, advice_date=date(2026, 7, day),
                        total=sum((x.amount for x in al), D(0)), lines=al,
                        confirmation_number=conf, tie_ok=tie_ok)


# ── name matching, the tie-break ──────────────────────────────────────────────

def test_name_score_survives_ncs_run_together_summaries():
    """NC writes "WeldreadyEFT-$476.94  13191" — no boundary between the vendor
    name and the payment detail, which is why both sides are squashed."""
    assert M.name_score("Weldready Inc.", "WeldreadyEFT-$476.94  13191") == 1.0
    assert M.name_score("Culligan Water", "Culligan of KingstonEFT-$27.95") > 0.5
    assert M.name_score("Linde Canada Inc", "Princess AutoEFT-$675.95") == 0.0


def test_name_score_handles_a_truncated_payee_column():
    """The bank truncates the payee column, and misspells: the real file says
    "Resource Porductivity" where NC says "Productivity". Partial credit on the
    matching prefix is right — it is a tie-break, and a typo should lower the
    score without zeroing it."""
    partial = M.name_score("Resource Porductivity and Reco",
                           "Resource Productivity and Recovery AuthorityEFT-$6.78")
    assert 0 < partial < 0.5
    assert M.name_score("MSC Industrial Supply ULC",
                        "MSC Industrial Supply ULCEFT-$30.21 8403563004") == 1.0


def test_description_score_finds_the_vendor_in_the_middle_of_boilerplate():
    """A payee name is a prefix; a bank description buries the identity in
    boilerplate, so it is scored per meaningful token instead."""
    assert M.description_score("Bill payment - 5144 WSIB-ON-SCHE1",
                               "WSIB Workplace Safety & InsuranceEFT-$10293.46") > 0
    assert M.description_score("Bill payment - 5144 WSIB-ON-SCHE1", "unrelated") == 0.0
    # the boilerplate alone must not make two unrelated lines look alike
    assert M.description_score("Bill payment - 8642 ZETA PROP",
                               "Bill Payment Hydro One") == 0.0


# ── rung 1: one advice, one statement line ────────────────────────────────────

def test_a_batch_clears_against_all_of_its_ledger_lines():
    """The 02 Jul case: one statement line, one payment file, 3 ledger lines."""
    adv = advice("7.2", 2, [("Alpha Supply", "340.75"), ("Beta Steel", "11997.21"),
                            ("Gamma Freight", "765.74")])
    txns = [bank("B1", 2, "Direct Deposits (PDS) service total GRADS9804420000", "-13103.70")]
    books = [book("K1", 3, "Alpha SupplyEFT-$340.75", "-340.75"),
             book("K2", 3, "Beta SteelEFT-$11997.21", "-11997.21"),
             book("K3", 3, "Gamma FreightEFT-$765.74", "-765.74")]
    p = M.plan(txns, [adv], books)
    assert len(p.groups) == 1
    g = p.groups[0]
    assert g.method == M.M_ADVICE_TOTAL
    assert g.bank_ids == ["B1"] and sorted(g.book_ids) == ["K1", "K2", "K3"]
    assert g.amount == D("-13103.70")
    assert len(p.advice_line_links) == 3
    assert p.unmatched_bank == [] and p.unmatched_book == []
    assert p.findings == []


def test_the_book_date_may_trail_the_bank_date():
    """The real 7.2 batch is bank date 02 Jul, NC voucher date 03 Jul."""
    adv = advice("7.2", 2, [("Alpha Supply", "340.75")])
    p = M.plan([bank("B1", 2, "PDS total", "-340.75")], [adv],
               [book("K1", 6, "Alpha SupplyEFT", "-340.75")])
    assert len(p.groups) == 1


def test_a_batch_outside_the_window_does_not_clear():
    adv = advice("7.2", 2, [("Alpha Supply", "340.75")])
    p = M.plan([bank("B1", 2, "PDS total", "-340.75")], [adv],
               [book("K1", 20, "Alpha SupplyEFT", "-340.75")])
    assert p.groups == []
    assert any(f.kind == "unbalanced" for f in p.findings)


# ── rung 2: two advices, one statement line ───────────────────────────────────

def test_two_payment_files_clearing_as_one_statement_line():
    """14 Jul = 7.13 (released the 13th) + 7.14. Two files, one bank line."""
    a13 = advice("7.13", 13, [("Alpha Supply", "54134.86")])
    a14 = advice("7.14", 14, [("Beta Steel", "64134.23")])
    txns = [bank("B1", 14, "Direct Deposits (PDS) service total", "-118269.09")]
    books = [book("K1", 14, "Alpha SupplyEFT-$54134.86", "-54134.86"),
             book("K2", 15, "Beta SteelEFT-$64134.23", "-64134.23")]
    p = M.plan(txns, [a13, a14], books)
    assert len(p.groups) == 1
    assert p.groups[0].method == M.M_ADVICE_COMBO
    assert sorted(p.groups[0].book_ids) == ["K1", "K2"]
    assert p.unmatched_bank == []


def test_two_equally_valid_advice_combinations_are_reported_not_chosen():
    a1 = advice("A", 14, [("X", "100.00")])
    a2 = advice("B", 14, [("Y", "200.00")])
    a3 = advice("C", 14, [("Z", "300.00")])
    # 100+200 and 300 alone... make two 2-sets tie instead:
    a4 = advice("Dd", 14, [("W", "200.00")])
    txns = [bank("B1", 14, "PDS total", "-300.00")]
    books = [book(k, 14, k, v) for k, v in
             (("K1", "-100.00"), ("K2", "-200.00"), ("K3", "-300.00"), ("K4", "-200.00"))]
    p = M.plan(txns, [a1, a2, a4], books)
    # A+B and A+Dd both make 300 -> ambiguous, and nothing is cleared on a guess
    assert any(f.kind == "ambiguous_advice_combo" for f in p.findings)
    assert all(g.method != M.M_ADVICE_COMBO for g in p.groups)


# ── rung 3: the confirmation number ───────────────────────────────────────────

def test_a_confirmation_number_clears_its_statement_line():
    adv = advice("7.2-bill", 2, [("ZETA PROPANE", "31.64")], kind="bill_payment", conf="8642")
    txns = [bank("B1", 2, "Bill payment - 8642 ZETA PROP", "-31.64")]
    books = [book("K1", 3, "Zeta Propane eft $31.64", "-31.64")]
    p = M.plan(txns, [adv], books)
    assert len(p.groups) == 1
    assert p.groups[0].method == M.M_CONFIRMATION
    assert "8642" in p.groups[0].note


def test_the_confirmation_number_wins_over_a_same_amount_batch():
    """Rung 3 runs first on purpose: a bill payment must not be swallowed by a
    batch that happens to total the same."""
    bill = advice("bill", 2, [("ZETA PROPANE", "31.64")], kind="bill_payment", conf="8642")
    batch = advice("batch", 2, [("Alpha", "31.64")])
    txns = [bank("B1", 2, "Bill payment - 8642 ZETA PROP", "-31.64"),
            bank("B2", 2, "Direct Deposits (PDS) service total", "-31.64")]
    books = [book("K1", 2, "Zeta Propane eft", "-31.64"),
             book("K2", 2, "AlphaEFT", "-31.64")]
    p = M.plan(txns, [bill, batch], books)
    by_bank = {g.bank_ids[0]: g for g in p.groups}
    assert by_bank["B1"].advice_id == "bill"
    assert by_bank["B1"].method == M.M_CONFIRMATION


# ── rung 4/6: inside a group ──────────────────────────────────────────────────

def test_one_advice_line_can_be_two_ledger_lines():
    """Ideal Supply 1,220.65 = 629.69 + 590.96 — a vendor-level merge."""
    adv = advice("7.22", 22, [("IDEAL SUPPLY INC", "1220.65")])
    txns = [bank("B1", 22, "PDS total", "-1220.65")]
    books = [book("K1", 22, "Ideal Supply INCEFT-$629.69", "-629.69"),
             book("K2", 22, "Ideal Supply INCEFT-$590.96", "-590.96")]
    p = M.plan(txns, [adv], books)
    assert len(p.groups) == 1
    assert sorted(p.groups[0].book_ids) == ["K1", "K2"]
    assert sorted(p.advice_line_links["7.22:1"]) == ["K1", "K2"]


def test_the_name_breaks_a_tie_between_equal_amounts():
    adv = advice("7.6", 6, [("Alpha Supply", "100.00")])
    txns = [bank("B1", 6, "PDS total", "-100.00")]
    books = [book("K1", 6, "Zeta HoldingsEFT-$100", "-100.00"),
             book("K2", 6, "Alpha SupplyEFT-$100", "-100.00")]
    p = M.plan(txns, [adv], books)
    assert p.groups and p.groups[0].book_ids == ["K2"]


def test_an_advice_that_cannot_be_fully_explained_does_not_clear():
    """The governing rule. Two of three ledger lines is not "mostly reconciled",
    it is a statement line whose remainder is unaccounted for."""
    adv = advice("7.2", 2, [("Alpha", "100.00"), ("Beta", "200.00"), ("Gamma", "300.00")])
    txns = [bank("B1", 2, "PDS total", "-600.00")]
    books = [book("K1", 2, "AlphaEFT", "-100.00"), book("K2", 2, "BetaEFT", "-200.00")]
    p = M.plan(txns, [adv], books)
    assert p.groups == []
    unb = [f for f in p.findings if f.kind == "unbalanced"]
    assert unb and "-600.00" in unb[0].message and "-300.00" in unb[0].message
    assert p.unmatched_bank == ["B1"]


# ── rung 5: one to one, no batch ──────────────────────────────────────────────

def test_a_transfer_and_a_bank_fee_clear_one_to_one():
    txns = [bank("B1", 7, "Funds transfer credit TT 1/CANADA ROY", "400000.00"),
            bank("B2", 7, "Activity fee", "-455.60")]
    books = [book("K1", 7, "BOC transfer to RBC CAD $400000", "400000.00", "bank_transfer"),
             book("K2", 7, "RBC CAD Bank charge", "-455.60", "bank_fee")]
    p = M.plan(txns, [], books)
    assert {g.method for g in p.groups} == {M.M_DIRECT}
    assert len(p.groups) == 2
    assert p.unmatched_bank == []


def test_rung_five_does_not_re_clear_what_a_batch_already_explained():
    adv = advice("7.2", 2, [("Alpha", "340.75")])
    txns = [bank("B1", 2, "PDS total", "-340.75")]
    books = [book("K1", 2, "AlphaEFT-$340.75", "-340.75")]
    p = M.plan(txns, [adv], books)
    assert len(p.groups) == 1                     # not two
    assert p.groups[0].method == M.M_ADVICE_TOTAL


def test_two_identical_candidates_with_no_name_signal_are_left_alone():
    txns = [bank("B1", 7, "Cheque - 23", "-2609.21")]
    books = [book("K1", 7, "something", "-2609.21"), book("K2", 7, "other", "-2609.21")]
    p = M.plan(txns, [], books)
    assert p.groups == []
    assert p.unmatched_bank == ["B1"]


# ── rung 6b: a statement line with no payment file ────────────────────────────

def test_a_line_with_no_payment_file_clears_but_is_reported():
    """The real 31 Jul case: 28,918.08 with no file supplied, and exactly two
    leftover ledger lines summing to it. Cleared — the ledger side balances to
    the cent — but never silently: its method and a finding both say so."""
    txns = [bank("B1", 31, "Direct Deposits (PDS) service total", "-28918.08")]
    books = [book("K1", 31, "Elevator CoEFT-$28209.32", "-28209.32"),
             book("K2", 30, "Freight CoEFT-$708.76", "-708.76")]
    p = M.plan(txns, [], books)
    assert len(p.groups) == 1
    assert p.groups[0].method == M.M_BOOK_SUBSET
    assert "no payment file" in p.groups[0].note
    flagged = [f for f in p.findings if f.kind == "no_advice"]
    assert flagged and flagged[0].bank_ids == ["B1"]


def test_two_equally_valid_ledger_subsets_are_reported_not_chosen():
    txns = [bank("B1", 31, "PDS total", "-300.00")]
    books = [book("K1", 31, "a", "-100.00"), book("K2", 31, "b", "-200.00"),
             book("K3", 31, "c", "-250.00"), book("K4", 31, "d", "-50.00")]
    p = M.plan(txns, [], books)
    assert p.groups == []
    assert any(f.kind == "ambiguous_book_subset" for f in p.findings)


# ── hygiene ───────────────────────────────────────────────────────────────────

def test_an_advice_that_does_not_tie_is_never_used():
    adv = advice("bad", 2, [("Alpha", "340.75")], tie_ok=False)
    p = M.plan([bank("B1", 2, "PDS total", "-340.75")], [adv],
               [book("K1", 2, "AlphaEFT", "-340.75")])
    assert all(g.advice_id != "bad" for g in p.groups)
    assert any(f.kind == "advice_not_tied" for f in p.findings)


def test_an_advice_with_no_statement_line_is_reported():
    adv = advice("7.9", 9, [("Alpha", "999.00")])
    p = M.plan([], [adv], [])
    assert any(f.kind == "advice_unused" for f in p.findings)


def test_nothing_is_claimed_twice_across_rungs():
    adv = advice("7.2", 2, [("Alpha", "100.00")])
    txns = [bank("B1", 2, "PDS total", "-100.00"), bank("B2", 2, "Something else", "-100.00")]
    books = [book("K1", 2, "AlphaEFT-$100", "-100.00")]
    p = M.plan(txns, [adv], books)
    used_bank = [b for g in p.groups for b in g.bank_ids]
    used_book = [b for g in p.groups for b in g.book_ids]
    assert len(used_bank) == len(set(used_bank))
    assert len(used_book) == len(set(used_book))
    assert p.unmatched_bank == ["B2"]


def test_an_empty_period_is_not_an_error():
    p = M.plan([], [], [])
    assert p.groups == [] and p.findings == []


def test_candidates_for_ranks_exact_amounts_first():
    txn = bank("B1", 20, "Bill payment - 5144 WSIB-ON-SCHE1", "-10293.46")
    books = [book("K1", 20, "unrelated", "-500.00"),
             book("K2", 21, "WSIB Workplace SafetyEFT-$10293.46", "-10293.46"),
             book("K3", 20, "WSIB something", "-99.00")]
    got = M.candidates_for(txn, books, claimed_book=set())
    assert got[0].id == "K2"                       # exact amount always first
    assert got[1].id == "K3"                       # then the one that shares "WSIB"
