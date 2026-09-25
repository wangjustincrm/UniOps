"""Bank statement PDF -> transactions, with an arithmetic gate in front of it.

Why this file is shaped the other way round from bank_advice_parse:

  Measured 2026-09-22 on the real RBC export with pypdf 5.1.0 — plain-mode text
  extraction gives 1661 chars over 47 lines, and **layout mode gives nothing at
  all**. So there is no positional data to read, and a statement's single most
  important fact is positional: which column an amount sits in. On the 31 Jul
  page, `BR TO BR - 2402  14,572.68` extracts adjacent to the debits and is a
  CREDIT (QuickBooks lists it under "Deposits and other credits cleared"). Text
  order cannot tell you that. Neither can a keyword: "Bill payment", "Misc
  Payment" and "Funds transfer credit" all describe money moving, in both
  directions.

So extraction is a vision read, and the thing that makes it safe is not the
model's confidence score — it is `verify()`, which checks the parse against the
statement's OWN printed anchors:

  opening + Σ signed amounts        == closing
  count of debits / credits         == the printed "Total cheques & debits (30)" / "(7)"
  Σ debits / Σ credits              == the printed totals
  every printed running balance     == opening + Σ amounts up to that line

RBC prints a running balance per date group (and an extra one mid-group after a
transfer credit), which makes the last check a per-line anchor rather than a
single end-to-end one: a statement whose lines are right but whose signs are
wrong fails at the first affected group, and the error names that line.

A statement that does not verify is stored unverified with its reasons and is
refused by the reconciliation. Nothing here is allowed to "nearly" balance.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from app.core.config import settings

log = logging.getLogger(__name__)

# Application AI is Haiku only, by standing rule — the key is shared with other
# tooling and an upgrade would starve EPMS invoice OCR.
MODEL = "claude-haiku-4-5-20251001"
ZERO = Decimal("0")
CENT = Decimal("0.01")


class StatementUnparseable(ValueError):
    """The document could not be read as a bank statement."""


@dataclass
class StatementLine:
    seq: int
    txn_date: date
    description: str
    # SIGNED: negative = money left the account. The single field the whole
    # verifier exists to protect.
    amount: Decimal
    # As printed, when the statement prints one on this line. None is normal —
    # RBC prints one per date group, not per line.
    running_balance: Decimal | None = None


@dataclass
class ParsedStatement:
    period_start: date
    period_end: date
    opening_balance: Decimal
    closing_balance: Decimal
    lines: list[StatementLine]
    currency: str = "CAD"
    account_no: str | None = None
    printed_total_debits: Decimal | None = None
    printed_total_credits: Decimal | None = None
    printed_debit_count: int | None = None
    printed_credit_count: int | None = None
    parse_method: str = "ai"
    parse_model: str | None = None
    verified: bool = False
    verify_errors: list[str] = field(default_factory=list)
    # What solve_signs could not settle. Each entry is a reason the statement is
    # not trustworthy, so verify() folds them into verify_errors.
    solve_notes: list[str] = field(default_factory=list)
    # "printed sections" when the directions came from the statement's own
    # deposit / withdrawal sections and those agree with its printed per-side
    # counts and totals — see _solve_by_printed_totals.
    direction_basis: str | None = None
    raw_payload: dict | None = None

    @property
    def debits(self) -> list[StatementLine]:
        return [ln for ln in self.lines if ln.amount < ZERO]

    @property
    def credits(self) -> list[StatementLine]:
        return [ln for ln in self.lines if ln.amount > ZERO]

    @property
    def total_debits(self) -> Decimal:
        return -sum((ln.amount for ln in self.debits), ZERO)

    @property
    def total_credits(self) -> Decimal:
        return sum((ln.amount for ln in self.credits), ZERO)


# ── the gate ───────────────────────────────────────────────────────────────────

def verify(st: ParsedStatement) -> tuple[bool, list[str]]:
    """Check a parse against the statement's own printed figures.

    Pure and AI-free on purpose: it is the same gate whether the lines came from
    a vision read, a CSV, or a human typing them in.
    """
    errors: list[str] = []

    if not st.lines:
        return False, ["No transactions were read from this statement."]

    # 1. the end-to-end identity
    computed = st.opening_balance + sum((ln.amount for ln in st.lines), ZERO)
    if computed != st.closing_balance:
        errors.append(
            f"Opening {st.opening_balance} plus the {len(st.lines)} transactions comes to "
            f"{computed}, but the statement closes at {st.closing_balance} "
            f"(off by {computed - st.closing_balance}).")

    # 2/3. the printed counts and totals, per side. Checked separately from the
    # identity above: two sign errors of equal size cancel in the sum and would
    # otherwise pass.
    if st.printed_debit_count is not None and len(st.debits) != st.printed_debit_count:
        errors.append(
            f"Read {len(st.debits)} debit lines but the statement prints "
            f"{st.printed_debit_count}.")
    if st.printed_credit_count is not None and len(st.credits) != st.printed_credit_count:
        errors.append(
            f"Read {len(st.credits)} credit lines but the statement prints "
            f"{st.printed_credit_count}.")
    if st.printed_total_debits is not None and st.total_debits != st.printed_total_debits:
        errors.append(
            f"Debits total {st.total_debits} but the statement prints "
            f"{st.printed_total_debits}.")
    if st.printed_total_credits is not None and st.total_credits != st.printed_total_credits:
        errors.append(
            f"Credits total {st.total_credits} but the statement prints "
            f"{st.printed_total_credits}.")

    # 4. the per-line anchors — what localises a sign error to a line
    running = st.opening_balance
    for ln in st.lines:
        running += ln.amount
        if ln.running_balance is not None and ln.running_balance != running:
            errors.append(
                f"Line {ln.seq} ({ln.txn_date} {ln.description[:40]!r}): the statement "
                f"shows a balance of {ln.running_balance} here, but the lines up to it "
                f"give {running}. The amount or its sign is wrong on this line or just "
                f"above it.")

    # 5. the residual blind spot, named rather than hoped away.
    #
    # Everything above compares the parse against printed figures. There is one
    # arrangement no printed figure can distinguish: inside a single balance
    # segment (the run of lines between two printed running balances), a debit and
    # a credit of the SAME magnitude can be swapped and leave the segment delta,
    # both counts and both per-side totals untouched. The statement simply does
    # not say which way round they go.
    #
    # It does not occur on the July RBC statement (no date group holds an equal
    # debit/credit pair), but "did not occur once" is not a guarantee, and a
    # silent coin-flip on direction is not acceptable for money. So: refuse, and
    # tell the human what to confirm.
    #
    # Not when the directions came from PRINTED SECTIONS. JPM US lists deposits
    # and withdrawals under their own headings, each with its count and total;
    # a 48,076.00 "EFT Return" under Deposits on 07/13 and a 48,076.00 "Corp Pay"
    # under Withdrawals on 07/16 are not ambiguous — the heading says which is
    # which, and the reading agreed with both printed totals. The check is for
    # layouts where the side is only a column position, which extraction loses.
    if st.direction_basis != "printed sections":
        errors.extend(_ambiguous_pairs(st))
    errors.extend(st.solve_notes)

    # 6. shape checks — cheap, and each one has been a real extraction failure
    for ln in st.lines:
        if ln.amount == ZERO:
            errors.append(f"Line {ln.seq} has a zero amount — a balance row read as a "
                          f"transaction: {ln.description[:60]!r}")
        if not (ln.description or "").strip():
            errors.append(f"Line {ln.seq} has no description.")
        if not (st.period_start <= ln.txn_date <= st.period_end):
            errors.append(
                f"Line {ln.seq} is dated {ln.txn_date}, outside the statement period "
                f"{st.period_start}..{st.period_end}.")

    if st.period_end < st.period_start:
        errors.append(f"Statement period ends ({st.period_end}) before it starts "
                      f"({st.period_start}).")

    st.verified = not errors
    st.verify_errors = errors
    return st.verified, errors


def _segments(st: ParsedStatement) -> list[list[StatementLine]]:
    """Lines grouped by printed running balance: each group ends on a line that
    carries one (the trailing group, if any, carries none)."""
    out: list[list[StatementLine]] = []
    cur: list[StatementLine] = []
    for ln in st.lines:
        cur.append(ln)
        if ln.running_balance is not None:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


# A segment wider than this is not enumerated. RBC's widest date group is 5
# lines; 16 is 65,536 sign vectors, which is instant and far past anything real.
_MAX_SOLVE_WIDTH = 16


def solve_signs(st: ParsedStatement) -> list[str]:
    """Recover each line's direction from the printed balances.

    Writes what it could not settle to `st.solve_notes` AND returns it — verify()
    reads the attribute, so a caller that forgets the return value still gets a
    statement that fails rather than one that silently passed.

    This exists because of what the model actually gets wrong. Measured on the
    real RBC July statement, one Haiku read returned all 36 lines with the right
    dates, descriptions, magnitudes AND running balances — and the signs
    systematically inverted (every "Direct Deposits (PDS) service total" as a
    credit, "BR TO BR" as a debit). Direction is the one field that has no textual
    signal, so asking a model for it and hoping is the wrong shape.

    But it is not guesswork either: the balances pin it down. Inside a segment —
    the run of lines between two printed balances — the signs must sum to that
    segment's delta. For segments this narrow, enumerate and see.

      unique solution  -> apply it; the model only had to read the numbers
      no solution      -> a MAGNITUDE is wrong; leave it and let verify() say so
      several          -> genuinely undetermined; leave it, _ambiguous_pairs reports

    Sign-solving therefore cannot mask a bad read. It only decides the one thing
    the page decides by column position, which is the thing extraction loses.
    """
    from itertools import product

    if all(ln.running_balance is None for ln in st.lines):
        by_totals = _solve_by_printed_totals(st)
        if by_totals is not None:
            st.solve_notes = by_totals
            return by_totals

    notes: list[str] = []
    balance = st.opening_balance
    for seg in _segments(st):
        end = seg[-1].running_balance
        if end is None:
            # The trailing run has no printed balance of its own; the statement's
            # closing balance is its anchor.
            end = st.closing_balance
        target = end - balance
        mags = [abs(ln.amount) for ln in seg]

        if len(seg) > _MAX_SOLVE_WIDTH:
            notes.append(
                f"Lines {seg[0].seq}-{seg[-1].seq}: {len(seg)} transactions with no "
                f"balance printed between them — too many to determine each direction "
                f"from the balances alone. Their signs were left as extracted.")
            balance = end
            continue

        solutions = [combo for combo in product((1, -1), repeat=len(seg))
                     if sum(s * m for s, m in zip(combo, mags)) == target]
        if len(solutions) == 1:
            for ln, sign, mag in zip(seg, solutions[0], mags):
                ln.amount = sign * mag
        elif not solutions:
            notes.append(
                f"Lines {seg[0].seq}-{seg[-1].seq}: no combination of these amounts "
                f"reaches the printed balance {end} (needs {target}). An amount was "
                f"misread, not just its direction.")
        else:
            notes.append(
                f"Lines {seg[0].seq}-{seg[-1].seq}: {len(solutions)} different "
                f"direction combinations all reach the printed balance {end}. The "
                f"statement does not say which — confirm these lines.")
        balance = end
    st.solve_notes = notes
    return notes


# Enumerating which k of n lines are the credits is C(n, k); past this many the
# statement is left to the per-segment path and its "too many" note.
_MAX_TOTALS_COMBINATIONS = 2_000_000


def _solve_by_printed_totals(st: ParsedStatement) -> list[str] | None:
    """Directions for a statement that prints NO running balance at all.

    JPM's US Commercial Checking lists "Deposits and Additions" and "Electronic
    Withdrawals" as separate sections and never prints a balance per line, so
    there is no segment to solve against — 17 lines in July, one segment, past
    what the balance solver will enumerate. But it prints, per side, how many
    and how much: 4 deposits for 266,572.29 and 13 withdrawals for 348,719.87.
    That is enough: the credits are the k-subset of magnitudes summing to the
    printed credit total. One such subset = the directions are proven by the
    statement's own figures, not by the model.

    Several subsets that differ only by swapping lines of EQUAL magnitude
    (48,076.00 returned and 48,076.00 paid) are the same arithmetic; the
    extracted signs are kept if they are one of them, and `_ambiguous_pairs`
    still names those lines for a human. Returns None when the statement does
    not print all four figures — the ordinary path applies.
    """
    from itertools import combinations
    from math import comb

    k, n = st.printed_credit_count, len(st.lines)
    if (st.printed_total_credits is None or st.printed_total_debits is None
            or k is None or st.printed_debit_count is None):
        return None
    if k + st.printed_debit_count != n:
        return [f"The statement prints {k} credits and {st.printed_debit_count} debits, "
                f"but {n} lines were read — a line is missing or extra."]
    if comb(n, k) > _MAX_TOTALS_COMBINATIONS:
        return None
    mags = [abs(ln.amount) for ln in st.lines]
    solutions = [set(c) for c in combinations(range(n), k)
                 if sum((mags[i] for i in c), ZERO) == st.printed_total_credits]
    if not solutions:
        return [f"No {k} of the {n} amounts add up to the printed credit total "
                f"{st.printed_total_credits} — an amount was misread."]
    shapes = {tuple(sorted(mags[i] for i in sol)) for sol in solutions}
    if len(shapes) > 1:
        return [f"{len(shapes)} different sets of {k} lines add up to the printed credit "
                f"total {st.printed_total_credits}. The statement does not say which — "
                f"confirm the directions."]
    extracted = {i for i, ln in enumerate(st.lines) if ln.amount > ZERO}
    if extracted in solutions:
        # The reader's own section placement agrees with both printed totals and
        # both counts: the directions ARE the statement's headings.
        st.direction_basis = "printed sections"
        return []
    # The reading disagreed with the printed totals, so its section placement is
    # not evidence. Take the arithmetic; equal-magnitude pairs stay flagged.
    chosen = solutions[0]
    for i, ln in enumerate(st.lines):
        ln.amount = mags[i] if i in chosen else -mags[i]
    return []


def _ambiguous_pairs(st: ParsedStatement) -> list[str]:
    """Equal-magnitude debit/credit pairs sharing one balance segment."""
    errors = []
    for seg in _segments(st):
        debit_mags = {-ln.amount: ln for ln in seg if ln.amount < ZERO}
        for ln in seg:
            if ln.amount > ZERO and ln.amount in debit_mags:
                other = debit_mags[ln.amount]
                errors.append(
                    f"Lines {min(ln.seq, other.seq)} and {max(ln.seq, other.seq)} are "
                    f"{ln.amount} in opposite directions with no balance printed between "
                    f"them ({other.description[:30]!r} / {ln.description[:30]!r}). The "
                    f"statement's own figures cannot tell which is the payment and which "
                    f"is the deposit — confirm the direction of these two lines.")
    return errors


# ── AI extraction ──────────────────────────────────────────────────────────────

_PROMPT = """\
You are reading a bank account statement PDF. Extract it as JSON.

Return EXACTLY this shape, no markdown, no commentary:
{
  "account_no": "string or null",
  "currency": "3-letter code",
  "period_start": "YYYY-MM-DD",
  "period_end": "YYYY-MM-DD",
  "opening_balance": number or null,
  "closing_balance": number or null,
  "printed_total_debits": number or null,
  "printed_total_credits": number or null,
  "printed_debit_count": integer or null,
  "printed_credit_count": integer or null,
  "lines": [
    {
      "date": "YYYY-MM-DD",
      "description": "string",
      "amount": number,
      "running_balance": number or null,
      "currency": "3-letter code or null"
    }
  ]
}

THE SIGN OF `amount` IS THE MOST IMPORTANT THING ON THIS PAGE. Get it from the
COLUMN the number is printed in, never from what the description says:
  - a number in the "Cheques & Debits" column is money LEAVING  -> NEGATIVE
  - a number in the "Deposits & Credits" column is money ARRIVING -> POSITIVE
  - a number in the "Balance" column is NOT a transaction. It is that line's
    `running_balance`. Never emit it as an amount.

Descriptions lie about direction. "Bill payment", "Misc Payment", "Funds transfer
credit", "BR TO BR" and "Direct Deposits (PDS) service total" all appear on both
sides on real statements. Only the column decides.

More rules:
  - One entry per printed transaction, in the order they appear. Do not merge
    lines that share a date, and do not invent a line for a subtotal.
  - A statement prints a running balance only on SOME lines (often the last of
    each date group). Put it on exactly those lines and null on the rest. Do not
    compute one yourself — a value you derived defeats the check it exists for.
  - Carry the date down: when a line shows no date it belongs to the date above.
  - `printed_total_debits` / `printed_total_credits` / the two counts come from
    the account summary, e.g. "Total cheques & debits (30) - 1,269,496.36" gives
    1269496.36 and 30. Null if the statement does not print them.
  - Amounts are plain numbers: no currency symbols, no thousands separators.
  - Include fees and interest — they are transactions like any other.
  - `closing_balance` is the statement's FINAL balance for the whole period
    ("Closing Balance", "Ending balance", "Your closing balance"). A figure
    labelled "Balance Carried Forward To Next Page" or "Balance Carried Over From
    Previous Page" is a page subtotal: it is NEVER the opening or the closing
    balance. Neither is "Total Dr", "Total Cr" or "Net Movement" — those are
    totals of the transactions, not balances. If THIS page does not print the
    final closing balance (it is often on the last page), return null for
    `closing_balance`.
  - If this page has no transaction table at all (a cover page of terms and
    addresses), return "lines": [] and null for anything it does not print.
"""


def _client():
    key = settings.bank_ai_key
    if not key:
        raise RuntimeError(
            "No AI key is configured for finance-api (BANK_ANTHROPIC_API_KEY or "
            "ANTHROPIC_API_KEY) — statement PDF reading is unavailable. Import the "
            "statement as CSV, or ask IT to set the key.")
    import anthropic
    return anthropic.Anthropic(api_key=key)


_ACCOUNT_LIMIT_MARKERS = (
    "usage limit", "credit balance", "quota", "billing", "spend limit",
    "insufficient", "organization",
)


def _raise_for_bad_request(exc: Exception) -> None:
    """Anthropic answers 400 both for "cannot read this document" and for
    account-level refusals. Conflating them told a finance clerk their statement
    was unreadable when the real cause was a billing ceiling — same split as
    expense-api's invoice OCR, and the same reason.
    """
    message = str(getattr(exc, "message", "") or exc)
    if any(m in message.lower() for m in _ACCOUNT_LIMIT_MARKERS):
        raise RuntimeError(
            "Statement reading is temporarily unavailable — the AI service account has "
            "reached a usage or billing limit. This is NOT a problem with your file. "
            f"(provider said: {message})") from exc
    raise StatementUnparseable(
        "Could not read this statement PDF. Check it is the statement itself (not a "
        "summary or a scan of one), or import the period as CSV instead.") from exc


def _dec(v, what: str) -> Decimal:
    if v is None:
        raise StatementUnparseable(f"{what} is missing from the extracted statement")
    try:
        return Decimal(str(v)).quantize(CENT)
    except (InvalidOperation, ValueError) as exc:
        raise StatementUnparseable(f"{what} is not a number: {v!r}") from exc


def _opt_dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v)).quantize(CENT)


def _day(v, what: str) -> date:
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError as exc:
        raise StatementUnparseable(f"{what} is not a YYYY-MM-DD date: {v!r}") from exc


def payload_to_statement(payload: dict) -> ParsedStatement:
    """Extraction JSON -> ParsedStatement. Structure only; `verify` judges it."""
    raw_lines = payload.get("lines") or []
    if not isinstance(raw_lines, list):
        raise StatementUnparseable("extracted 'lines' is not a list")
    lines = []
    for i, ln in enumerate(raw_lines, start=1):
        if not isinstance(ln, dict):
            raise StatementUnparseable(f"extracted line {i} is not an object")
        lines.append(StatementLine(
            seq=i,
            txn_date=_day(ln.get("date"), f"line {i} date"),
            description=str(ln.get("description") or "").strip()[:500],
            amount=_dec(ln.get("amount"), f"line {i} amount"),
            running_balance=_opt_dec(ln.get("running_balance")),
        ))
    st = ParsedStatement(
        period_start=_day(payload.get("period_start"), "period_start"),
        period_end=_day(payload.get("period_end"), "period_end"),
        opening_balance=_dec(payload.get("opening_balance"), "opening_balance"),
        closing_balance=_dec(payload.get("closing_balance"), "closing_balance"),
        lines=lines,
        currency=(payload.get("currency") or "CAD")[:10],
        account_no=(payload.get("account_no") or None),
        printed_total_debits=_opt_dec(payload.get("printed_total_debits")),
        printed_total_credits=_opt_dec(payload.get("printed_total_credits")),
        printed_debit_count=payload.get("printed_debit_count"),
        printed_credit_count=payload.get("printed_credit_count"),
        raw_payload=payload,
    )
    # Solve directions from the balances BEFORE verifying: the extracted signs are
    # not evidence, the balances are. solve_signs records its own notes.
    solve_signs(st)
    verify(st)
    return st


_CONTINUATION_PROMPT = """\
This is page {page} of a multi-page bank statement. Return ONLY the transaction
rows printed in the "Account Activity Details" table ON THIS PAGE, as JSON:

{{"opening_balance": number or null,
 "closing_balance": number or null,
 "lines": [{{"date": "YYYY-MM-DD", "description": "string", "amount": number,
             "running_balance": number or null, "currency": "3-letter code or null"}}]}}

`opening_balance`: only if THIS page prints the statement's opening balance
("Opening Balance", "Balance forward" at the start of the period); else null.

`closing_balance`: the statement's FINAL closing balance, only if THIS page
prints it ("Closing Balance", "Ending balance"). "Balance Carried Forward To Next
Page" / "Balance Carried Over From Previous Page" are page subtotals, and "Total
Dr" / "Total Cr" / "Net Movement" are totals of the transactions — none of them
is the closing balance. Null when this page does not print the final one.

Also, ONLY if this page prints them (else null): "period_start" and "period_end"
("STATEMENT PERIOD FROM 1 JUL 2026 TO 31 JUL 2026" -> "2026-07-01", "2026-07-31"),
"account_no", and "currency" (the statement's currency).

The sign rule, the balance-column rule and the date carry-down rule are the same
as for page 1:
  - "Cheques & Debits" column -> NEGATIVE; "Deposits & Credits" column -> POSITIVE
  - a "Balance" figure is this line's `running_balance`, NEVER an amount
  - a line with no date belongs to the date above it
  - never compute a running_balance yourself; null means the page printed none

Read every row on this page, including the last one — a row at the very bottom
edge counts. Do NOT repeat rows from other pages, and do not emit the
opening/closing balance summary, the page header, or any marketing text.
Return {{"lines": []}} if this page has no transaction table.
"""


def _ask(content, what: str) -> dict:
    """One Haiku call -> parsed JSON object. All the error translation lives here."""
    import anthropic

    client = _client()
    try:
        resp = client.messages.create(model=MODEL, max_tokens=16384,
                                      messages=[{"role": "user", "content": content}])
    except anthropic.BadRequestError as exc:
        _raise_for_bad_request(exc)
    except anthropic.APIConnectionError as exc:
        raise RuntimeError(f"Could not reach the AI service: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise RuntimeError(
            "The AI service is rate-limited right now. Try again in a minute.") from exc
    except anthropic.APIStatusError as exc:
        raise RuntimeError(f"AI service error {exc.status_code}: {exc}") from exc

    if resp.stop_reason == "max_tokens":
        raise StatementUnparseable(
            f"{what} has more transactions than one read can return. Split the PDF "
            f"into smaller page ranges and import the parts.")

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("statement extraction returned non-JSON for %s: %s", what, text[:400])
        raise StatementUnparseable(
            f"The AI response for {what} could not be read as statement data. Try "
            f"again, or import the period as CSV.") from exc
    if not isinstance(payload, dict):
        raise StatementUnparseable(f"The AI response for {what} was not an object.")
    return payload


def _doc_block(pdf_bytes: bytes) -> dict:
    import base64
    return {"type": "document",
            "source": {"type": "base64", "media_type": "application/pdf",
                       "data": base64.standard_b64encode(pdf_bytes).decode("utf-8")}}


def _single_pages(pdf_bytes: bytes) -> list[bytes]:
    """Split into one-page PDFs, so each read sees one page and nothing else."""
    import io

    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(pdf_bytes))
    if reader.is_encrypted:
        reader.decrypt("")
    out = []
    for page in reader.pages:
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        out.append(buf.getvalue())
    return out


def _currency_rule(currency: str | None) -> str:
    """Appended to every page's prompt when the account's currency is known.

    The reader is asked to TRANSCRIBE every currency section's balances with
    their currency code — not to pick one. Picking is done in code
    (`_page_balances`), because asked to "return null when this page has no USD
    closing", Haiku returned the CAD closing on ICBC page 2 anyway.
    """
    if not currency:
        return ""
    return f"""

THIS ACCOUNT IS IN {currency}. Some banks (ICBC) print several currency
sub-accounts in ONE statement, one after another — each with its own "Opening
Balance" row, its own transactions and its own "Closing Balance" row, with a
currency code at the start of every row.

Add this key to your JSON:
  "balance_sections": [
    {{"currency": "3-letter code", "opening_balance": number or null,
      "closing_balance": number or null}}
  ]
with ONE entry for EVERY currency whose opening or closing balance is printed ON
THIS PAGE, each with its own currency code, exactly as printed — even the
currencies that are not {currency}. Use null for a balance this page does not
print for that currency. "Balance Carried Forward To Next Page" and portfolio /
account summary tables are not opening or closing balances.

For `lines`, return ONLY {currency} transactions. "Opening Balance", "Closing
Balance", "Total Withdrawal" and "Total Deposit" rows are balances and totals,
never transactions. Put each transaction's currency code in `currency` (null
when the row does not print one).
"""


def _page_balances(reply: dict, ccy: str | None) -> tuple:
    """(opening, closing) for the account's currency, from one page's reply.

    With a currency: take the matching section. A page whose sections are ALL in
    other currencies contributes nothing — that is what stops ICBC page 2's CAD
    closing from becoming the USD account's closing. A single section with no
    code is a single-currency statement and is taken as-is.
    """
    secs = reply.get("balance_sections")
    if ccy and isinstance(secs, list) and secs:
        secs = [x for x in secs if isinstance(x, dict)]
        mine = [x for x in secs if str(x.get("currency") or "").upper() == ccy]
        if mine:
            opening = next((x.get("opening_balance") for x in mine
                            if x.get("opening_balance") is not None), None)
            closing = next((x.get("closing_balance") for x in reversed(mine)
                            if x.get("closing_balance") is not None), None)
            return opening, closing
        if len(secs) == 1 and not secs[0].get("currency"):
            return secs[0].get("opening_balance"), secs[0].get("closing_balance")
        return None, None
    return reply.get("opening_balance"), reply.get("closing_balance")


def extract_payload(pdf_bytes: bytes, filename: str = "statement.pdf",
                    currency: str | None = None) -> dict:
    """Haiku reads -> the extraction JSON. No verification here.

    Read PAGE BY PAGE, not whole-document, and the reason is measured. A single
    whole-document read of the real 3-page RBC statement got lines 1-33 perfect —
    every printed running balance reproduced to the cent — and dropped exactly one
    row: the 28,918.08 batch that sits at the very bottom of page 2, where the
    31 Jul group breaks across the page boundary. One missing line out of 37, at a
    seam. Splitting the PDF removes the seam, and it also removes the max_tokens
    cliff on a long statement.

    Page 1 carries the account summary (opening, closing, the printed totals and
    counts) so it is read with the full prompt; later pages are lines-only.
    """
    pages = _single_pages(pdf_bytes)
    if not pages:
        raise StatementUnparseable(f"{filename} has no pages.")

    ccy = currency.upper() if currency else None
    rule = _currency_rule(ccy)
    payload = _ask([_doc_block(pages[0]), {"type": "text", "text": _PROMPT + rule}],
                   f"{filename} page 1")
    lines = list(payload.get("lines") or [])
    o, c = _page_balances(payload, ccy)
    openings, closings = [(1, o)], [(1, c)]
    for i, page in enumerate(pages[1:], start=2):
        more = _ask([_doc_block(page),
                     {"type": "text",
                      "text": _CONTINUATION_PROMPT.format(page=i) + rule}],
                    f"{filename} page {i}")
        lines.extend(more.get("lines") or [])
        o, c = _page_balances(more, ccy)
        openings.append((i, o))
        closings.append((i, c))
        # JPM Toronto's page 1 is terms and addresses only; the period, account
        # number and currency are in the header of page 2 onwards.
        for key in ("period_start", "period_end", "account_no", "currency"):
            if not payload.get(key) and more.get(key):
                payload[key] = more[key]
    if ccy:
        # The prompt asks for one currency; this is the check that it got one.
        # A row that names a DIFFERENT currency is dropped, and counted.
        kept = [ln for ln in lines if not isinstance(ln, dict)
                or not ln.get("currency") or str(ln["currency"]).upper() == ccy]
        payload["_other_currency_lines_dropped"] = len(lines) - len(kept)
        lines = kept
        payload["currency"] = ccy
    payload["lines"] = lines
    payload["_pages_read"] = len(pages)
    _settle_opening(payload, openings, filename)
    _settle_closing(payload, closings, filename)
    return payload


def _as_dec(v) -> Decimal | None:
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except Exception:
        return None


def _settle_opening(payload: dict, openings: list, filename: str) -> None:
    """The FIRST page that printed an opening balance wins — the mirror of the
    closing rule. On a single-currency statement that is page 1, as always; on a
    combined one (ICBC) the account's section can begin on any page."""
    printed = [(page, v) for page, v in openings if v is not None]
    if not printed:
        raise StatementUnparseable(
            f"{filename}: no page prints an opening balance"
            f"{' for ' + payload['currency'] if payload.get('currency') else ''}"
            f" — is this the right account's statement?")
    page, value = printed[0]
    payload["opening_balance"] = value
    payload["_opening_from"] = f"page {page}"


def _settle_closing(payload: dict, closings: list, filename: str) -> None:
    """Which page's closing balance is THE closing balance.

    Page 1 alone is not enough. RBC prints the account summary — closing included
    — on page 1, but Bank of China prints "Closing Balance" at the end of the LAST
    page, and page 1 only has "Balance Carried Forward To Next Page". Read from
    page 1, BOC CNY July 2026 closed at 37,804.68 (the page-1 carry-forward)
    instead of 20,851.68, and the statement came back unverified — and because
    signs are solved from the balances, a wrong closing can flip them too.

    So: the LAST page that printed a closing balance wins. If none did, the last
    running balance the bank printed is the closing (it is the balance after the
    final transaction), and that choice is recorded. If there is neither, say so
    rather than guess.
    """
    printed = [(page, v) for page, v in closings if v is not None]
    lines = payload.get("lines") or []
    last = lines[-1] if lines else None
    last_balance = (last.get("running_balance") if isinstance(last, dict) else None)
    if printed and last_balance is not None:
        # The closing balance IS the balance after the last transaction. When the
        # bank printed that balance, it arbitrates between candidates: JPM August
        # offered "Net Movement 43,172.17" from its last page, and the real
        # closing 168,522.18 is the last row's balance.
        agreeing = [(page, v) for page, v in printed
                    if _as_dec(v) is not None and _as_dec(v) == _as_dec(last_balance)]
        if agreeing:
            printed = agreeing
        else:
            # JPM Toronto prints no closing-balance field at all — only the last
            # row's balance, then "Total Dr / Total Cr / Net Movement". Every
            # candidate the pages offered was one of those totals (July: Net
            # Movement 41,810.24 for a closing of 125,350.01). The bank's own last
            # balance wins; the rejected figures are kept for the record. A
            # misread last balance is still caught — verify() checks every
            # printed balance against the lines above it.
            payload["closing_balance"] = last_balance
            payload["_closing_from"] = "last printed running balance"
            payload["_closing_rejected"] = [f"page {pg}: {v}" for pg, v in printed]
            return
    if printed:
        page, value = printed[-1]
        payload["closing_balance"] = value
        payload["_closing_from"] = f"page {page}"
        return
    if isinstance(last, dict) and last.get("running_balance") is not None:
        payload["closing_balance"] = last["running_balance"]
        payload["_closing_from"] = "last printed running balance"
        return
    raise StatementUnparseable(
        f"{filename}: no page prints a closing balance and the last transaction "
        f"has no printed balance — the closing balance cannot be read.")


def parse_statement(pdf_bytes: bytes, filename: str = "statement.pdf",
                    currency: str | None = None) -> ParsedStatement:
    """PDF -> a verified-or-explained ParsedStatement.

    Never raises on a verification failure: an unverified statement is worth
    storing and showing (its `verify_errors` name the line that is wrong), and the
    reconciliation is what refuses to use it.
    """
    payload = extract_payload(pdf_bytes, filename=filename, currency=currency)
    st = payload_to_statement(payload)
    st.parse_method = "ai"
    st.parse_model = MODEL
    return st
