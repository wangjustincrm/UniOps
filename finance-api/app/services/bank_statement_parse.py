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
  "opening_balance": number,
  "closing_balance": number,
  "printed_total_debits": number or null,
  "printed_total_credits": number or null,
  "printed_debit_count": integer or null,
  "printed_credit_count": integer or null,
  "lines": [
    {
      "date": "YYYY-MM-DD",
      "description": "string",
      "amount": number,
      "running_balance": number or null
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

{{"lines": [{{"date": "YYYY-MM-DD", "description": "string", "amount": number,
             "running_balance": number or null}}]}}

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


def extract_payload(pdf_bytes: bytes, filename: str = "statement.pdf") -> dict:
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

    payload = _ask([_doc_block(pages[0]), {"type": "text", "text": _PROMPT}],
                   f"{filename} page 1")
    lines = list(payload.get("lines") or [])
    for i, page in enumerate(pages[1:], start=2):
        more = _ask([_doc_block(page),
                     {"type": "text", "text": _CONTINUATION_PROMPT.format(page=i)}],
                    f"{filename} page {i}")
        lines.extend(more.get("lines") or [])
    payload["lines"] = lines
    payload["_pages_read"] = len(pages)
    return payload


def parse_statement(pdf_bytes: bytes, filename: str = "statement.pdf") -> ParsedStatement:
    """PDF -> a verified-or-explained ParsedStatement.

    Never raises on a verification failure: an unverified statement is worth
    storing and showing (its `verify_errors` name the line that is wrong), and the
    reconciliation is what refuses to use it.
    """
    payload = extract_payload(pdf_bytes, filename=filename)
    st = payload_to_statement(payload)
    st.parse_method = "ai"
    st.parse_model = MODEL
    return st
