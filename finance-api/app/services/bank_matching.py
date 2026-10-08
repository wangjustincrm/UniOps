"""The matching ladder: statement lines against NC's ledger, via payment advices.

Pure functions over plain dataclasses — no database, no I/O — so every rung can
be tested against the real July shapes without a fixture factory.

## What the ladder is for

A statement line is usually a merged payment. "Direct Deposits (PDS) service
total 48,336.03" on 02 Jul is one payment-advice PDF holding 18 vendor payments,
each of which is its own NC ledger line. Sometimes it is two advices released on
different days (14 Jul = 7.13 + 7.14). Sometimes one advice line is itself two
ledger lines (Ideal Supply 1,220.65 = 629.69 + 590.96). Nothing here is
one-to-one, so every rung produces a GROUP: N statement lines against M ledger
lines.

## The rungs, in order

  1 advice_total    one advice total == one statement line          12 of 18 PDS lines
  2 advice_combo    2-3 advice totals sum to one statement line     14 Jul, 31 Jul
  3 confirmation_no a bill payment's confirmation number appears    8642, 5386
                    in the statement description
  4 advice_line     inside a group, each advice line -> its ledger  234 of 237
                    line by exact amount (name breaks ties)
  5 direct          one ledger line <-> one statement line, no      transfers, Hydro One,
                    batch involved                                  the activity fee
  6 subset_sum      an advice line is several ledger lines, found   Ideal Supply, Yonger
                    by bounded subset-sum in one vendor bucket
  6b book_subset    a statement line with NO payment file, against   the 31 Jul 28,918.08
                    a unique small subset of leftover ledger lines   (28,209.32 + 708.76)
  6c (finding)      the one leftover statement line and the one leftover   JPM US 224.18,
                    ledger line with this amount in the whole period,     11 days apart
                    outside the window — NAMED for a person, not cleared
  6d bank_rollup    ALL leftover statement lines sum to the one leftover   JPM Toronto July:
                    ledger line — NC booked a month of card settlements   39 Paymentech /
                    as one entry                                          PayPal lines =
                                                                          shopify 41,180.87
  7 (none)          left for a human, with candidates ranked

Measured end to end on the real July set — the AI-read statement, the 19 payment
files, and NC's own 260 ledger lines for RBC CAD: **37 of 37 statement lines and
260 of 260 ledger lines cleared, all 237 advice lines linked**, with one finding,
correctly naming the statement line whose payment file was not supplied.

## The rule that governs all of them

A group is proposed only when **the ledger side sums to exactly the bank side**.
A rung that can explain the statement line but cannot account for every cent of
it produces an `unbalanced` finding instead of a match — with what it did find,
so a human starts from the shortfall rather than from nothing. Clearing a bank
line against a ledger total that merely looks close is how a reconciliation ends
up hiding the thing it exists to reveal.

Each statement line and each ledger line can be claimed once. The claim sets are
also what stop rung 5 from re-clearing something rung 1 already explained.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import combinations

ZERO = Decimal("0")

# Book/bank dates routinely differ — the 7.2 batch is NC voucher date 07-03 and
# bank date 02 Jul, and the QuickBooks report shows book 07/30 rows clearing on
# the 31 Jul statement. Five days covers a weekend plus a day either side.
DEFAULT_WINDOW_DAYS = 5

# Rung 2: how many advices may be summed into one statement line. The real cases
# are two; three is headroom. Beyond that the combinatorics stop being evidence
# and start being coincidence.
MAX_ADVICE_COMBO = 3

# Rung 6: the most ledger lines that may be summed into one advice line, and the
# most candidates the bucket may hold before it is abandoned as ambiguous.
MAX_SUBSET_SIZE = 3
MAX_SUBSET_CANDIDATES = 8

# Rung 6b, the no-advice case: how many ledger lines may be inferred behind a
# statement line that has no payment file, and how big the candidate pool may get
# before the search stops being evidence. Four and forty, deliberately small —
# a real batch of eighteen is not something to infer, it is something to upload
# the payment file for.
MAX_BOOK_SUBSET_SIZE = 4
MAX_BOOK_SUBSET_POOL = 40

M_ADVICE_TOTAL = "advice_total"
M_ADVICE_COMBO = "advice_combo"
M_CONFIRMATION = "confirmation_no"
M_DIRECT = "direct"
M_SUBSET_SUM = "subset_sum"
M_BOOK_SUBSET = "book_subset"
M_BANK_ROLLUP = "bank_rollup"
M_MANUAL = "manual"          # a person built the group by hand

# Legal-form noise that differs between the bank's payee list and NC's summary
# text ("Weldready Inc." vs "WeldreadyEFT-$476.94").
_SUFFIXES = ("INCORPORATED", "CORPORATION", "LIMITED", "COMPANY", "ULC", "INC",
             "LTD", "LTEE", "LLC", "LP", "CO", "CORP", "GMBH", "SA", "NV", "BV")
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def squash(text: str | None) -> str:
    """Uppercase alphanumerics only.

    NC summaries run the vendor name straight into the payment detail —
    "WeldreadyEFT-$476.94  13191" — so there is no token boundary to split on.
    Squashing both sides turns the question into substring containment, which is
    the only comparison that survives that.
    """
    return _NON_ALNUM.sub("", (text or "").upper())


def _strip_suffixes(name: str) -> str:
    out = squash(name)
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIXES:
            if len(out) > len(suf) + 3 and out.endswith(suf):
                out, changed = out[: -len(suf)], True
    return out


def name_score(payee: str | None, summary: str | None) -> float:
    """0..1 — how much a bank payee looks like an NC line's summary text.

    Only ever a TIE-BREAK. Rung 4 matches on exact amount first; this decides
    between several ledger lines that share that amount, and a score of 0 never
    creates a match on its own.
    """
    a, b = _strip_suffixes(payee), squash(summary)
    if not a or not b:
        return 0.0
    if a in b:
        return 1.0
    # A truncated payee column ("RESOURCE PORDUCTIVI") against the full name in
    # the summary: score on the longest prefix of the payee that appears.
    for n in range(len(a), 5, -1):
        if a[:n] in b:
            return n / len(a)
    return 0.0


# Words a bank puts on every second line. They carry no identity, and leaving
# them in makes "Bill payment - 5144 WSIB-ON-SCHE1" look like "Bill Payment Hydro
# One" — which is exactly the kind of near-miss a human should never be offered
# at the top of a candidate list.
_DESC_STOPWORDS = {
    "BILL", "PAYMENT", "PAYMENTS", "PMT", "EFT", "DEPOSIT", "DEPOSITS", "DIRECT",
    "SERVICE", "TOTAL", "MISC", "FEE", "FEES", "CREDIT", "DEBIT", "CHEQUE",
    "CHECK", "FUNDS", "TRANSFER", "FILE", "PAY", "COMM", "INT", "TT", "THE",
    "AND", "FOR", "FROM", "INC", "LTD", "ULC", "CORP",
}


def _tokens(text: str | None) -> list[str]:
    return [t for t in _NON_ALNUM.split((text or "").upper())
            if len(t) >= 4 and t not in _DESC_STOPWORDS]


def description_score(description: str | None, summary: str | None) -> float:
    """0..1 — how much a bank DESCRIPTION looks like an NC summary.

    Separate from name_score because the two sides are shaped differently. A
    payee name is a prefix ("Weldready Inc." against "WeldreadyEFT-$476.94"), so
    containment is the right test there. A bank description buries the identity
    in the middle of boilerplate — "Bill payment - 5144 WSIB-ON-SCHE1" — so the
    test has to be per token, over tokens that mean something.

    Used for ranking the candidates a human is offered, and as a tie-break when
    two ledger lines share an amount. Never enough on its own to clear a line.
    """
    toks = _tokens(description)
    if not toks:
        return 0.0
    hay = squash(summary)
    if not hay:
        return 0.0
    return sum(1 for t in toks if t in hay) / len(toks)


@dataclass
class BankTxn:
    id: object
    txn_date: date
    description: str
    amount: Decimal          # signed; negative = money left the account
    currency: str = "CAD"


@dataclass
class AdviceLineView:
    id: object
    payee_name: str | None
    amount: Decimal          # positive


@dataclass
class AdviceView:
    id: object
    kind: str                # pds_batch | bill_payment
    advice_date: date
    total: Decimal           # positive
    lines: list[AdviceLineView]
    confirmation_number: str | None = None
    client_number: str | None = None
    tie_ok: bool = True


@dataclass
class BookLineView:
    id: object
    voucher_date: date
    summary: str | None
    amount: Decimal          # signed, same convention as BankTxn
    contra_kind: str = "unknown"
    jv_number: str | None = None


@dataclass
class ProposedGroup:
    method: str
    bank_ids: list
    book_ids: list
    amount: Decimal
    advice_id: object | None = None
    note: str | None = None


@dataclass
class Finding:
    """Something the ladder could explain but not balance, or not explain at all.
    Carried to the UI so a human starts from the shortfall."""
    kind: str
    bank_ids: list = field(default_factory=list)
    advice_ids: list = field(default_factory=list)
    book_ids: list = field(default_factory=list)
    message: str = ""


@dataclass
class MatchPlan:
    groups: list[ProposedGroup] = field(default_factory=list)
    # advice line id -> book line id, for the vendor-level drill-down
    advice_line_links: dict = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    unmatched_bank: list = field(default_factory=list)
    unmatched_book: list = field(default_factory=list)


def _within(a: date, b: date, window: int) -> bool:
    return abs((a - b).days) <= window


def _resolve_advice_lines(advice: AdviceView, books: list[BookLineView],
                          claimed_book: set, window: int) -> tuple[list, dict, list]:
    """One advice -> the ledger lines behind it.

    Returns (book_ids, advice_line_links, unresolved_advice_lines).

    Rung 4 then rung 6, per advice line:
      - exact amount within the window; several candidates are decided by name,
        and a tie with no name signal is left alone rather than guessed
      - failing that, a bounded subset of same-vendor lines that sums to it —
        which is what Ideal Supply 1,220.65 = 629.69 + 590.96 needs
    """
    book_ids: list = []
    links: dict = {}
    unresolved: list = []
    taken = set(claimed_book)

    for aline in advice.lines:
        want = -aline.amount          # a payment leaves the account
        pool = [b for b in books
                if b.id not in taken
                and _within(b.voucher_date, advice.advice_date, window)]

        exact = [b for b in pool if b.amount == want]
        chosen = None
        if len(exact) == 1:
            chosen = exact[0]
        elif len(exact) > 1:
            scored = sorted(((name_score(aline.payee_name, b.summary), b) for b in exact),
                            key=lambda sb: -sb[0])
            if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                chosen = scored[0][1]
        if chosen is not None:
            taken.add(chosen.id)
            book_ids.append(chosen.id)
            links[aline.id] = chosen.id
            continue

        # Rung 6 — one vendor payment, several ledger lines.
        bucket = [b for b in pool if name_score(aline.payee_name, b.summary) >= 0.6]
        if 1 < len(bucket) <= MAX_SUBSET_CANDIDATES:
            hit = _subset_summing_to(bucket, want)
            if hit:
                for b in hit:
                    taken.add(b.id)
                    book_ids.append(b.id)
                links[aline.id] = [b.id for b in hit]
                continue
        unresolved.append(aline)

    return book_ids, links, unresolved


def _subset_summing_to(pool: list[BookLineView], target: Decimal) -> list | None:
    """The one subset that hits `target`, or None. Several subsets means the data
    does not decide it, so nothing is returned."""
    found = None
    for size in range(2, min(MAX_SUBSET_SIZE, len(pool)) + 1):
        for combo in combinations(pool, size):
            if sum((b.amount for b in combo), ZERO) == target:
                if found is not None:
                    return None
                found = list(combo)
    return found


def plan(bank_txns: list[BankTxn], advices: list[AdviceView],
         book_lines: list[BookLineView],
         window_days: int = DEFAULT_WINDOW_DAYS) -> MatchPlan:
    """Run the ladder. Nothing is written; the caller persists what it accepts."""
    out = MatchPlan()
    claimed_bank: set = set()
    claimed_book: set = set()
    claimed_advice: set = set()

    usable = [a for a in advices if a.tie_ok]
    for a in advices:
        if not a.tie_ok:
            out.findings.append(Finding(
                kind="advice_not_tied", advice_ids=[a.id],
                message="This payment file does not add up to its own printed total, so "
                        "it cannot be used to explain a statement line. Re-import it."))

    def commit(method, txns, advice, book_ids, links, note=None):
        bank_total = sum((t.amount for t in txns), ZERO)
        book_total = sum((_book_by_id[b].amount for b in book_ids), ZERO)
        if book_total != bank_total:
            out.findings.append(Finding(
                kind="unbalanced", bank_ids=[t.id for t in txns],
                advice_ids=[advice.id] if advice else [], book_ids=list(book_ids),
                message=(f"The statement line comes to {bank_total} but the ledger lines "
                         f"found for it come to {book_total} — a difference of "
                         f"{bank_total - book_total}. Not cleared; the missing side needs "
                         f"a look.")))
            return False
        out.groups.append(ProposedGroup(
            method=method, bank_ids=[t.id for t in txns], book_ids=list(book_ids),
            amount=bank_total, advice_id=advice.id if advice else None, note=note))
        out.advice_line_links.update(links)
        claimed_bank.update(t.id for t in txns)
        claimed_book.update(book_ids)
        if advice is not None:
            claimed_advice.add(advice.id)
        return True

    _book_by_id = {b.id: b for b in book_lines}

    # ── rung 3 first: a confirmation number is an exact join key, and it is
    # cheaper and stronger than any amount search. Doing it before the amount
    # rungs also stops a bill payment being swallowed by a same-amount batch.
    for advice in usable:
        if not advice.confirmation_number or advice.id in claimed_advice:
            continue
        needle = squash(advice.confirmation_number)
        hits = [t for t in bank_txns
                if t.id not in claimed_bank
                and needle and needle in squash(t.description)
                and _within(t.txn_date, advice.advice_date, window_days)]
        if len(hits) != 1:
            continue
        books, links, unresolved = _resolve_advice_lines(
            advice, book_lines, claimed_book, window_days)
        commit(M_CONFIRMATION, hits, advice, books, links,
               note=f"confirmation number {advice.confirmation_number}")

    # ── rung 1: one advice total is one statement line
    for advice in usable:
        if advice.id in claimed_advice:
            continue
        hits = [t for t in bank_txns
                if t.id not in claimed_bank and t.amount == -advice.total
                and _within(t.txn_date, advice.advice_date, window_days)]
        if len(hits) != 1:
            continue
        books, links, unresolved = _resolve_advice_lines(
            advice, book_lines, claimed_book, window_days)
        commit(M_ADVICE_TOTAL, hits, advice, books, links)

    # ── rung 2: several advices sum to one statement line (a file released on
    # the 13th and one on the 14th clearing together on the 14th)
    free = [a for a in usable if a.id not in claimed_advice]
    for txn in [t for t in bank_txns if t.id not in claimed_bank and t.amount < ZERO]:
        near = [a for a in free
                if a.id not in claimed_advice
                and _within(a.advice_date, txn.txn_date, window_days)]
        combo = None
        for size in range(2, MAX_ADVICE_COMBO + 1):
            hits = [c for c in combinations(near, size)
                    if sum((a.total for a in c), ZERO) == -txn.amount]
            if len(hits) == 1:
                combo = hits[0]
                break
            if len(hits) > 1:
                out.findings.append(Finding(
                    kind="ambiguous_advice_combo", bank_ids=[txn.id],
                    advice_ids=[a.id for c in hits for a in c],
                    message=(f"{len(hits)} different sets of payment files add up to this "
                             f"statement line. Pick the right one.")))
                break
        if not combo:
            continue
        books: list = []
        links: dict = {}
        for advice in combo:
            b, lk, _unres = _resolve_advice_lines(advice, book_lines, claimed_book | set(books),
                                                  window_days)
            books.extend(b)
            links.update(lk)
        bank_total = txn.amount
        book_total = sum((_book_by_id[b].amount for b in books), ZERO)
        if book_total != bank_total:
            out.findings.append(Finding(
                kind="unbalanced", bank_ids=[txn.id], advice_ids=[a.id for a in combo],
                book_ids=books,
                message=(f"The statement line comes to {bank_total} but the ledger lines "
                         f"found for its {len(combo)} payment files come to {book_total}.")))
            continue
        out.groups.append(ProposedGroup(
            method=M_ADVICE_COMBO, bank_ids=[txn.id], book_ids=books, amount=bank_total,
            advice_id=combo[0].id,
            note=f"{len(combo)} payment files cleared together"))
        out.advice_line_links.update(links)
        claimed_bank.add(txn.id)
        claimed_book.update(books)
        claimed_advice.update(a.id for a in combo)

    # ── rung 5: one ledger line against one statement line, no batch involved.
    # Transfers, bank charges, a utility paid directly.
    for txn in [t for t in bank_txns if t.id not in claimed_bank]:
        cands = [b for b in book_lines
                 if b.id not in claimed_book and b.amount == txn.amount
                 and _within(b.voucher_date, txn.txn_date, window_days)]
        if len(cands) == 1:
            commit(M_DIRECT, [txn], None, [cands[0].id], {})
        elif len(cands) > 1:
            scored = sorted(((description_score(txn.description, b.summary), b)
                             for b in cands), key=lambda sb: -sb[0])
            if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                commit(M_DIRECT, [txn], None, [scored[0][1].id], {},
                       note="matched on description")

    # ── rung 6b: a statement line with no payment file behind it.
    #
    # Measured on July: the 28,918.08 batch on 31 Jul had no payment file in the
    # set, and the two ledger lines left over (28,209.32 + 708.76) are exactly it.
    # Inferring that is legitimate — the ledger side balances to the cent — but it
    # is a weaker kind of evidence than an advice, so it is recorded under its own
    # method AND reported, so "this line has no payment file" stays visible rather
    # than disappearing into a cleared count.
    for txn in [t for t in bank_txns if t.id not in claimed_bank]:
        pool = [b for b in book_lines
                if b.id not in claimed_book
                and (b.amount < ZERO) == (txn.amount < ZERO)
                and _within(b.voucher_date, txn.txn_date, window_days)]
        if not (1 < len(pool) <= MAX_BOOK_SUBSET_POOL):
            continue
        hit = None
        for size in range(2, MAX_BOOK_SUBSET_SIZE + 1):
            found = [c for c in combinations(pool, size)
                     if sum((b.amount for b in c), ZERO) == txn.amount]
            if len(found) == 1:
                hit = found[0]
                break
            if len(found) > 1:
                out.findings.append(Finding(
                    kind="ambiguous_book_subset", bank_ids=[txn.id],
                    book_ids=[b.id for c in found for b in c],
                    message=(f"{len(found)} different sets of {size} ledger lines add up to "
                             f"this statement line. Upload its payment file, or pick the "
                             f"right set by hand.")))
                break
        if not hit:
            continue
        commit(M_BOOK_SUBSET, [txn], None, [b.id for b in hit], {},
               note="no payment file for this line — ledger lines inferred from the amount")
        out.findings.append(Finding(
            kind="no_advice", bank_ids=[txn.id], book_ids=[b.id for b in hit],
            message=(f"This statement line ({txn.amount}) has no payment file. It was "
                     f"cleared against {len(hit)} ledger lines that sum to it exactly, but "
                     f"upload the payment file to confirm the vendor breakdown.")))

    # ── rung 6c: same amount, too far apart to clear — so NAME it, don't clear it.
    #
    # JPM US July: the bank returned 224.18 on 07-17 and NC booked "returned
    # Buhler Technologies usd$224.18" on 07-28 — eleven days, past the window.
    # The window is deliberate (an AP payment 18 days off the same amount can be
    # last month's invoice paid again), so this does not clear anything. But when
    # the amount is left exactly ONCE on each side for the whole period, the pair
    # is the obvious candidate and the person should be handed it, not made to
    # hunt for it.
    left_bank = [t for t in bank_txns if t.id not in claimed_bank]
    left_book = [b for b in book_lines if b.id not in claimed_book]
    wide_pairs = set()
    for txn in left_bank:
        books = [b for b in left_book if b.amount == txn.amount]
        banks = [t for t in left_bank if t.amount == txn.amount]
        if len(books) == 1 and len(banks) == 1:
            gap = abs((books[0].voucher_date - txn.txn_date).days)
            wide_pairs.update({txn.id, books[0].id})
            out.findings.append(Finding(
                kind="wide_date_candidate", bank_ids=[txn.id], book_ids=[books[0].id],
                message=(f"{txn.amount} on {txn.txn_date} is the only statement line of this "
                         f"amount, and {books[0].jv_number} on {books[0].voucher_date} the "
                         f"only ledger line — {gap} days apart, too far to clear on the "
                         f"amount. If they are the same money, tick both and Match these.")))

    # ── rung 6d: many statement lines against ONE ledger line.
    #
    # The mirror of 6b. JPM Toronto July: 38 card-settlement deposits (Paymentech,
    # PayPal, AlphaPay) and one Paymentech fee on the statement; NC booked the
    # month as a single "shopify Payment Jul" 41,180.87 — which is exactly what
    # all 39 leftover statement lines add up to. Only the whole leftover set is
    # tried (no subset search over dozens of lines), and only against a ledger
    # line that is the sole one of that amount; reported, like 6b, so a
    # month-end rollup stays visible instead of vanishing into a cleared count.
    # Lines just named in 6c are held out: they have an explanation waiting.
    left_bank = [t for t in bank_txns if t.id not in claimed_bank and t.id not in wide_pairs]
    left_book = [b for b in book_lines if b.id not in claimed_book and b.id not in wide_pairs]
    if len(left_bank) > 1 and left_book:
        total = sum((t.amount for t in left_bank), ZERO)
        hits = [b for b in left_book if b.amount == total]
        if len(hits) == 1:
            book = hits[0]
            if commit(M_BANK_ROLLUP, left_bank, None, [book.id], {},
                      note=f"{len(left_bank)} statement lines booked in NC as one entry"):
                out.findings.append(Finding(
                    kind="bank_rollup", bank_ids=[t.id for t in left_bank],
                    book_ids=[book.id],
                    message=(f"{len(left_bank)} statement lines ({left_bank[0].txn_date} to "
                             f"{left_bank[-1].txn_date}) add up exactly to one ledger line, "
                             f"{book.jv_number} {total} — NC booked them as one entry. "
                             f"Cleared on the amount; check that is what that entry is.")))

    out.unmatched_bank = [t.id for t in bank_txns if t.id not in claimed_bank]
    out.unmatched_book = [b.id for b in book_lines if b.id not in claimed_book]
    for advice in usable:
        if advice.id not in claimed_advice:
            out.findings.append(Finding(
                kind="advice_unused", advice_ids=[advice.id],
                message=(f"This payment file ({advice.total}) does not match any statement "
                         f"line in the period. It may have cleared in a different month, or "
                         f"its statement line is missing.")))
    return out


def candidates_for(txn: BankTxn, book_lines: list[BookLineView], claimed_book: set,
                   window_days: int = DEFAULT_WINDOW_DAYS, limit: int = 20) -> list:
    """Ranked suggestions for a statement line a human has to clear by hand.

    Exact amount first, then same-sign lines near the date ranked by description
    similarity. Rung 7 of the ladder is a person, and this is what it hands them.
    """
    pool = [b for b in book_lines if b.id not in claimed_book]
    exact = [b for b in pool if b.amount == txn.amount]
    near = [b for b in pool
            if b.amount != txn.amount
            and (b.amount < ZERO) == (txn.amount < ZERO)
            and _within(b.voucher_date, txn.txn_date, window_days)]
    near.sort(key=lambda b: (-description_score(txn.description, b.summary),
                             abs((b.voucher_date - txn.txn_date).days)))
    return (exact + near)[:limit]
