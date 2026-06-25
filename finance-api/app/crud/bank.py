"""Bank statement import, reconciliation matching, FX lookup (Phase a A3)."""
import csv
import hashlib
import io
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank import (
    EXCLUDED, MATCHED, UNMATCHED, BankAccount, BankTransaction, ExchangeRate,
)
from app.models.payment import PaymentRecord

_ZERO = Decimal("0")


# ── CSV import ──────────────────────────────────────────────────────────────────

def _import_hash(account_id: uuid.UUID, txn_date: str, amount: str, desc: str, ref: str) -> str:
    raw = f"{account_id}|{txn_date}|{amount}|{desc}|{ref}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _money(raw: str) -> Decimal:
    """Parse a money cell tolerant of $, commas, parens-negatives, trailing CR/DR."""
    s = (raw or "").strip()
    if not s:
        return _ZERO
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("$", "").replace(",", "").replace("−", "-").strip()
    upper = s.upper()
    if upper.endswith("CR"):
        s = s[:-2].strip()
    elif upper.endswith("DR"):
        s, neg = s[:-2].strip(), True
    if not s or s in {"-", "."}:
        return _ZERO
    val = Decimal(s)
    return -val if neg else val


def _parse_date(raw: str, fmt: str | None, default_year: int | None) -> date:
    """ISO by default; else strptime with `fmt`. `default_year` fills formats
    that omit the year (e.g. RBC '01 May' with fmt '%d %b')."""
    s = (raw or "").strip()
    if not fmt:
        return date.fromisoformat(s)
    dt = datetime.strptime(s, fmt)
    if default_year and dt.year == 1900 and "%y" not in fmt.lower():
        dt = dt.replace(year=default_year)
    return dt.date()


def _normalize_row(raw: dict, m: dict, cols: dict) -> tuple[str, Decimal, str, str]:
    """Apply a column mapping to one CSV row → (date_str, signed_amount, desc, ref).
    m keys: date, description, reference?, amount? | debit?+credit?,
    date_format?, default_year?, debit_sign? ('negative' default | 'positive')."""
    def cell(key: str) -> str:
        col = m.get(key)
        return (raw.get(cols.get((col or "").strip().lower(), "")) or "").strip() if col else ""

    d_raw = cell("date")
    desc = cell("description")
    ref = cell("reference")
    if m.get("amount"):
        amount = _money(cell("amount"))
    else:
        debit = _money(cell("debit"))
        credit = _money(cell("credit"))
        # debit = outflow (negative) by default; credit = inflow (positive)
        debit = abs(debit)
        credit = abs(credit)
        amount = credit - debit if m.get("debit_sign") != "positive" else debit - credit
    txn_date = _parse_date(d_raw, m.get("date_format"), m.get("default_year"))
    return txn_date.isoformat(), amount, desc, ref


# default generic mapping (back-compat: date,amount,description[,reference] signed)
_GENERIC_MAPPING = {"date": "date", "amount": "amount",
                    "description": "description", "reference": "reference"}


async def import_statement_csv(
    db: AsyncSession, account: BankAccount, csv_text: str,
    mapping: dict | None = None,
) -> dict:
    """Import a statement CSV using a column mapping (A3 workbench). When no
    mapping is given, falls back to the account's saved preset, then the generic
    signed-amount shape. Rows that net to zero (header carry-overs, balance-only
    lines) are skipped. Duplicate rows (same import_hash) are skipped."""
    m = mapping or account.import_mapping or _GENERIC_MAPPING
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return {"imported": 0, "duplicates": 0, "skipped": 0, "errors": ["Empty file"]}
    cols = {f.strip().lower(): f for f in reader.fieldnames}

    has_amount = bool(m.get("amount"))
    needed = [m.get("date"), m.get("description")]
    needed += [m.get("amount")] if has_amount else [m.get("debit"), m.get("credit")]
    missing = [c for c in needed if c and c.strip().lower() not in cols]
    if missing or not all([m.get("date"), m.get("description")]):
        return {"imported": 0, "duplicates": 0, "skipped": 0,
                "errors": [f"Mapped columns not found in CSV: {missing or 'date/description required'}"]}

    imported = duplicates = skipped = 0
    errors: list[str] = []
    seen_in_batch: set[str] = set()
    for n, raw in enumerate(reader, start=2):
        try:
            d, amount, desc, ref = _normalize_row(raw, m, cols)
        except (ValueError, InvalidOperation, KeyError) as e:
            errors.append(f"Line {n}: {e}")
            continue
        if amount == _ZERO:
            skipped += 1  # balance carry-forward / non-money row
            continue
        h = _import_hash(account.id, d, str(amount), desc, ref)
        if h in seen_in_batch:
            duplicates += 1
            continue
        seen_in_batch.add(h)
        existing = (await db.execute(
            select(BankTransaction).where(BankTransaction.import_hash == h)
        )).scalar_one_or_none()
        if existing is not None:
            duplicates += 1
            continue
        db.add(BankTransaction(
            bank_account_id=account.id, txn_date=date.fromisoformat(d), description=desc[:500],
            reference=ref or None, amount=amount, currency=account.currency,
            status=UNMATCHED, import_hash=h, entity_id=account.entity_id,
        ))
        imported += 1
    await db.flush()
    return {"imported": imported, "duplicates": duplicates, "skipped": skipped, "errors": errors}


# ── Reconciliation matching ─────────────────────────────────────────────────────

async def _claimed_payment_ids(db: AsyncSession) -> set[uuid.UUID]:
    rows = (await db.execute(
        select(BankTransaction.matched_payment_id)
        .where(BankTransaction.matched_payment_id.is_not(None))
    )).scalars().all()
    return set(rows)


async def auto_match(db: AsyncSession, account: BankAccount, window_days: int = 5) -> dict:
    """Match unmatched OUTFLOW lines to payment_records: same currency, exact
    amount (|txn| == payment.amount), payment not already claimed, within the
    date window. Unique candidate → auto-match; ambiguous → reference-disambig
    or leave for a human (never guess)."""
    txns = (await db.execute(
        select(BankTransaction).where(
            BankTransaction.bank_account_id == account.id,
            BankTransaction.status == UNMATCHED,
            BankTransaction.amount < _ZERO,
        )
    )).scalars().all()
    claimed = await _claimed_payment_ids(db)

    matched = 0
    ambiguous = 0
    for txn in txns:
        target = -txn.amount  # outflow magnitude
        lo, hi = txn.txn_date - timedelta(days=window_days), txn.txn_date + timedelta(days=window_days)
        candidates = (await db.execute(
            select(PaymentRecord).where(
                PaymentRecord.amount == target,
                PaymentRecord.currency == txn.currency,
                PaymentRecord.payment_date >= lo,
                PaymentRecord.payment_date <= hi,
            )
        )).scalars().all()
        candidates = [c for c in candidates if c.id not in claimed]
        if not candidates:
            continue
        chosen = None
        if len(candidates) == 1:
            chosen = candidates[0]
        elif txn.reference:
            # disambiguate by reference containing the doc/pa number
            ref = txn.reference.lower()
            hits = [c for c in candidates
                    if (c.doc_number and c.doc_number.lower() in ref)
                    or (c.pa_number and c.pa_number.lower() in ref)
                    or (c.reference and c.reference.lower() in ref)]
            if len(hits) == 1:
                chosen = hits[0]
        if chosen is None:
            ambiguous += 1
            continue
        txn.status = MATCHED
        txn.matched_payment_id = chosen.id
        txn.matched_at = datetime.now(timezone.utc)
        claimed.add(chosen.id)
        matched += 1
    await db.flush()
    return {"matched": matched, "ambiguous": ambiguous, "scanned": len(txns)}


async def manual_match(db: AsyncSession, txn: BankTransaction,
                       payment_id: uuid.UUID, actor_id: uuid.UUID) -> BankTransaction:
    """Human override — amount mismatch allowed (bank fees etc.); actor recorded."""
    pay = (await db.execute(
        select(PaymentRecord).where(PaymentRecord.id == payment_id)
    )).scalar_one_or_none()
    if pay is None:
        raise LookupError("Payment record not found")
    claimed = await _claimed_payment_ids(db)
    if payment_id in claimed and txn.matched_payment_id != payment_id:
        raise ValueError("That payment is already matched to another statement line")
    txn.status = MATCHED
    txn.matched_payment_id = payment_id
    txn.matched_at = datetime.now(timezone.utc)
    txn.matched_by = actor_id
    await db.flush()
    return txn


async def unmatch(db: AsyncSession, txn: BankTransaction) -> BankTransaction:
    txn.status = UNMATCHED
    txn.matched_payment_id = None
    txn.matched_at = None
    txn.matched_by = None
    await db.flush()
    return txn


async def set_excluded(db: AsyncSession, txn: BankTransaction, excluded: bool) -> BankTransaction:
    """Mark a line as excluded (bank fee, interest, internal FX transfer) so it
    drops off the discrepancy list without needing a payment match. Reversible."""
    txn.status = EXCLUDED if excluded else UNMATCHED
    txn.matched_payment_id = None
    txn.matched_at = None
    txn.matched_by = None
    await db.flush()
    return txn


# ── FX lookup ───────────────────────────────────────────────────────────────────

async def rate_on(db: AsyncSession, from_ccy: str, as_of: date,
                  to_ccy: str = "CAD") -> Decimal | None:
    """Most recent rate effective on/before as_of. Same currency → 1."""
    if from_ccy == to_ccy:
        return Decimal("1")
    row = (await db.execute(
        select(ExchangeRate).where(
            ExchangeRate.from_currency == from_ccy,
            ExchangeRate.to_currency == to_ccy,
            ExchangeRate.effective_date <= as_of,
        ).order_by(ExchangeRate.effective_date.desc())
    )).scalars().first()
    return row.rate if row else None
