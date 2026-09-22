"""Payment-advice (payment file) parsing — deterministic first, AI only on a miss.

The bank portal hands back two shapes after a payment run:

  1. "Payment File Content" — a PDS batch. One row per vendor, and a printed
     footer `Number Of Payments : 18  Total:  48,336.03`. This total is what the
     bank statement shows as a single "Direct Deposits (PDS) service total" line.
  2. "Released Bill Payment Confirmation Numbers" — bill payments, each with a
     **confirmation number** that appears verbatim in the statement description
     ("Bill payment - 8642 TYENDINAGA PROP").

Order of preference is the opposite of the statement parser's, and the evidence
is why: measured 2026-09-22 against all 19 July files, plain text extraction
reproduces every file's printed total exactly (19/19, 237 lines, 1,031,087.99
total). So the regex parser runs first and the AI extractor is only reached when
the tie test fails — which keeps a month-end batch off the shared API key
entirely.

The tie test is the contract:
  - PDS batch:    Σ line amounts == printed Total  AND  count == printed N
  - bill payment: row indices are 1..N consecutive (these files print no total)

A parse that does not tie is stored with `tie_ok = False` and its reason, and the
matcher refuses to use it. Nothing here is allowed to "nearly" balance.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.services import bank_pdf

log = logging.getLogger(__name__)

PDS_BATCH = "pds_batch"
BILL_PAYMENT = "bill_payment"

_PDS_MARKER = "payment file content"
_BILL_MARKERS = ("released bill payment confirmation", "payment confirmation")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

# "Report Creation Date : Jul  02,  2026 03:36:25 PM ET" — the spacing is erratic
# in the source, so every gap is \s+.
_RE_REPORT_DATE = re.compile(
    r"Report\s+Creation\s+Date\s*:?\s*([A-Za-z]{3})[a-z]*\s+(\d{1,2})\s*,\s*(\d{4})", re.I)
# "Client Number : 9804420000 - PDS CAD - CAN ROYAL MILK"; the statement shows the
# same digits as "GRADS9804420000", which makes this a matching signal too.
_RE_CLIENT_NO = re.compile(r"Client\s+Number\s*:?\s*([0-9]{4,20})", re.I)
_RE_HEADER_CCY = re.compile(r"Client\s+Number\s*:.*?-\s*PDS\s+([A-Z]{3})", re.I)
# "Number Of Payments :   18 Total:            48,336.03"
_RE_FOOTER = re.compile(
    r"Number\s+Of\s+Payments\s*:?\s*(\d+)\s*Total\s*:?\s*([\d,]+\.\d{2})", re.I)
_RE_CCY = re.compile(r"^[A-Z]{3}$")
_RE_PAYNUM = re.compile(r"^\d{2}$")
_RE_AMOUNT = re.compile(r"^[\d,]+\.\d{2}$")
# "1 TYENDINAGA PROPANE-153579 31.64 Jul 02, 2026 8642 Completed"
_RE_BILL_ROW = re.compile(
    r"^\s*(?P<idx>\d+)\s+(?P<who>.+?)\s+(?P<amt>[\d,]+\.\d{2})\s+"
    r"(?P<mon>[A-Za-z]{3})[a-z]*\s+(?P<day>\d{1,2})\s*,\s*(?P<year>\d{4})\s+"
    r"(?P<conf>\d+)\s+(?P<status>[A-Za-z]+)\s*$")

# Lines that are chrome, not data. Checked before the continuation rule, so a
# footer is never glued onto the previous vendor's name.
_CHROME = re.compile(
    r"^\s*(?:$|_+$|\d+\s*$"
    r"|Payment\s+File\s+Content|Report\s+Creation\s+Date|Client\s+Number"
    r"|Amount\s+Range|Payment\s+Group|Status\s*:|Environment\s*:"
    r"|Customer\s+Number|Number\s+Of\s+Payments|Destination\s+Country"
    r"|Released\s+Bill\s+Payment|Payment\s+Confirmation|Corporate\s+Creditor"
    r"|Amount\s*$|Payment\s+Date|Confirmation\s*$|Number\s*$|Status\s*$"
    r"|\*\*\*)", re.I)


class AdviceUnparseable(ValueError):
    """Not a payment file we recognise at all."""


@dataclass
class AdviceLine:
    seq: int
    payee_code: str | None
    payee_name: str
    currency: str
    amount: Decimal


@dataclass
class ParsedAdvice:
    kind: str
    advice_date: date
    currency: str
    lines: list[AdviceLine]
    client_number: str | None = None
    confirmation_number: str | None = None
    printed_total: Decimal | None = None
    printed_count: int | None = None
    tie_ok: bool = False
    tie_error: str | None = None
    parse_method: str = "text"
    raw_text: str = ""
    # Bill-payment files carry one confirmation number PER ROW; a file with
    # several becomes several advices. Filled by parse_advice.
    per_row_confirmations: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((ln.amount for ln in self.lines), Decimal("0"))


def _money(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", "").strip())
    except InvalidOperation as exc:
        raise AdviceUnparseable(f"not an amount: {raw!r}") from exc


def _mdy(mon: str, day: str, year: str) -> date:
    m = _MONTHS.get(mon[:3].lower())
    if not m:
        raise AdviceUnparseable(f"unknown month {mon!r}")
    return date(int(year), m, int(day))


def _fields(line: str) -> list[str]:
    """One layout-mode line -> its columns.

    pypdf's layout mode pads columns out to the PDF's user-space width, so the
    gaps between fields are dozens of spaces while gaps *inside* a field are
    single spaces. Splitting on two-or-more spaces therefore recovers the real
    columns: "ACKLANDS GRAINGER ... CAD ... 00 ... 340.75" ->
    ['ACKLANDS GRAINGER', 'ACKLANDS GRAINGER', 'CAD', '00', '340.75'].

    This is why the parser reads layout mode and not plain: plain returns the
    whole page as one blob with the vendor names run together
    ("...GRAINGERACKLANDS GRAINGER CAD00 340.75INCAMAZON..."), which no amount of
    regex recovers correctly.
    """
    return [f for f in re.split(r"\s{2,}", line.strip()) if f]


def _pds_row(fields: list[str]) -> tuple[str | None, str, str, Decimal] | None:
    """Columns -> (payee_code, payee_name, currency, amount), or None.

    A vendor row always ends in the three fixed columns Destination Currency /
    Payment Number / Amount. Anything before them is the customer number and name
    — two columns in the source, occasionally merged by the extractor, in which
    case the whole thing becomes the name rather than inventing a boundary.
    """
    if len(fields) < 4:
        return None
    ccy, paynum, amt = fields[-3], fields[-2], fields[-1]
    if not (_RE_CCY.match(ccy) and _RE_PAYNUM.match(paynum) and _RE_AMOUNT.match(amt)):
        return None
    who = fields[:-3]
    code = who[0] if len(who) > 1 else None
    name = " ".join(who[1:]) if len(who) > 1 else who[0]
    return code, name, ccy, _money(amt)


def detect_kind(text: str) -> str | None:
    head = text[:1500].lower()
    if _PDS_MARKER in head:
        return PDS_BATCH
    if any(m in head for m in _BILL_MARKERS):
        return BILL_PAYMENT
    return None


def parse_pds_batch(text: str) -> ParsedAdvice:
    """Rows, then the printed footer, then the tie test."""
    lines_out: list[AdviceLine] = []
    printed_total = printed_count = None

    for raw in text.splitlines():
        footer = _RE_FOOTER.search(raw)
        if footer:
            printed_count = int(footer.group(1))
            printed_total = _money(footer.group(2))
            continue
        if _CHROME.match(raw):
            continue
        row = _pds_row(_fields(raw))
        if row:
            code, name, ccy, amount = row
            lines_out.append(AdviceLine(
                seq=len(lines_out) + 1, payee_code=code, payee_name=name,
                currency=ccy, amount=amount))
            continue
        # Not chrome, not a row, and we already have one: a wrapped vendor name.
        # The source truncates the name column, so "Federal Express Canada" and
        # its overflow "Corpora" arrive as two lines. Gluing them back keeps the
        # name usable for the similarity tiebreak; it can never move an amount.
        frag = raw.strip()
        if lines_out and frag and len(frag) <= 40 and not any(ch.isdigit() for ch in frag):
            prev = lines_out[-1]
            prev.payee_name = f"{prev.payee_name} {frag}".strip()

    if not lines_out:
        raise AdviceUnparseable("no vendor rows found in this payment file")

    m = _RE_REPORT_DATE.search(text)
    if not m:
        raise AdviceUnparseable("no 'Report Creation Date' — cannot date this payment file")
    ccy_m = _RE_HEADER_CCY.search(text)
    cno = _RE_CLIENT_NO.search(text)

    adv = ParsedAdvice(
        kind=PDS_BATCH,
        advice_date=_mdy(m.group(1), m.group(2), m.group(3)),
        currency=(ccy_m.group(1) if ccy_m else lines_out[0].currency),
        lines=lines_out,
        client_number=cno.group(1) if cno else None,
        printed_total=printed_total, printed_count=printed_count,
        raw_text=text,
    )
    adv.tie_ok, adv.tie_error = _tie_pds(adv)
    return adv


def _tie_pds(adv: ParsedAdvice) -> tuple[bool, str | None]:
    if adv.printed_total is None or adv.printed_count is None:
        return False, ("This payment file prints no 'Number Of Payments / Total' footer, "
                       "so the parse cannot be checked against the bank's own figures.")
    problems = []
    if adv.total != adv.printed_total:
        problems.append(f"lines sum to {adv.total} but the file prints {adv.printed_total} "
                        f"(off by {adv.total - adv.printed_total})")
    if len(adv.lines) != adv.printed_count:
        problems.append(f"read {len(adv.lines)} vendor rows but the file prints "
                        f"{adv.printed_count}")
    return (not problems), ("; ".join(problems) or None)


def _bill_row(fields: list[str]) -> tuple[int, "AdviceLine", str, date] | None:
    """Layout columns of a released-bill-payment row ->
    (index, line, confirmation_number, payment_date).

    Columns: index, "CREDITOR-account", amount, "Mon DD, YYYY", confirmation, status.
    The index and the creditor sometimes share a column ("1 TYENDINAGA
    PROPANE-153579"), so the leading integer is peeled off the first field.
    """
    if len(fields) < 5:
        return None
    flat = " ".join(fields)
    return _bill_row_regex(flat)


def _bill_row_regex(line: str) -> tuple[int, "AdviceLine", str, date] | None:
    row = _RE_BILL_ROW.match(line.strip())
    if not row:
        return None
    who = row.group("who").strip()
    # The creditor field is "NAME-accountnumber"; keep the account as the code and
    # the name as the name, which is what the ledger summary will look like.
    code, name = (who.rsplit("-", 1)[1], who.rsplit("-", 1)[0]) if "-" in who else (None, who)
    idx = int(row.group("idx"))
    return (idx,
            AdviceLine(seq=idx, payee_code=code, payee_name=name,
                       currency="CAD", amount=_money(row.group("amt"))),
            row.group("conf"),
            _mdy(row.group("mon"), row.group("day"), row.group("year")))


def parse_bill_payment(text: str) -> ParsedAdvice:
    """One row per released bill payment. No printed total — the tie test is that
    the row indices are 1..N with nothing missing."""
    rows: list[tuple[int, AdviceLine, str, date]] = []
    for raw in text.splitlines():
        parsed = _bill_row(_fields(raw)) or _bill_row_regex(raw)
        if parsed:
            rows.append(parsed)
    if not rows:
        raise AdviceUnparseable("no bill-payment rows found in this file")

    rows.sort(key=lambda r: r[0])
    m = _RE_REPORT_DATE.search(text)
    advice_date = (_mdy(m.group(1), m.group(2), m.group(3)) if m else rows[0][3])

    adv = ParsedAdvice(
        kind=BILL_PAYMENT,
        advice_date=advice_date,
        currency="CAD",
        lines=[r[1] for r in rows],
        # One confirmation number per row. A single-row file — every real one in
        # the July set — gets it on the advice; a multi-row file keeps them all in
        # per_row_confirmations so the importer can split it.
        confirmation_number=rows[0][2] if len(rows) == 1 else None,
        per_row_confirmations=[r[2] for r in rows],
        raw_text=text,
    )
    indices = [r[0] for r in rows]
    if indices != list(range(1, len(indices) + 1)):
        adv.tie_ok, adv.tie_error = False, (
            f"row numbers are {indices} — expected 1..{len(indices)}, so a row was "
            "missed or read twice")
    else:
        adv.tie_ok, adv.tie_error = True, None
    return adv


def parse_text(text: str) -> ParsedAdvice:
    kind = detect_kind(text)
    if kind == PDS_BATCH:
        return parse_pds_batch(text)
    if kind == BILL_PAYMENT:
        return parse_bill_payment(text)
    raise AdviceUnparseable(
        "This does not look like a bank payment file. Expected either "
        "'Payment File Content' or 'Released Bill Payment Confirmation Numbers'.")


def parse_advice(pdf_bytes: bytes) -> ParsedAdvice:
    """PDF -> ParsedAdvice. Raises AdviceUnparseable for a file we don't know.

    LAYOUT mode, not plain: see bank_pdf.page_texts for the measurement that
    settles it.
    """
    return parse_text(bank_pdf.full_text(pdf_bytes, mode=bank_pdf.LAYOUT))


def split_bill_payments(adv: ParsedAdvice) -> list[ParsedAdvice]:
    """A multi-row bill-payment file is N advices, because the STATEMENT sees N
    separate lines — one per confirmation number. Splitting here keeps the
    matcher's "one advice, one confirmation number" invariant true.

    A PDS batch is the opposite and is never split: its whole point is that the
    statement sees one merged line.
    """
    if adv.kind != BILL_PAYMENT or len(adv.lines) <= 1:
        return [adv]
    out = []
    for ln, conf in zip(adv.lines, adv.per_row_confirmations):
        one = ParsedAdvice(
            kind=BILL_PAYMENT, advice_date=adv.advice_date, currency=adv.currency,
            lines=[AdviceLine(seq=1, payee_code=ln.payee_code, payee_name=ln.payee_name,
                              currency=ln.currency, amount=ln.amount)],
            client_number=adv.client_number, confirmation_number=conf,
            per_row_confirmations=[conf],
            tie_ok=adv.tie_ok, tie_error=adv.tie_error,
            parse_method=adv.parse_method, raw_text=adv.raw_text,
        )
        out.append(one)
    return out
