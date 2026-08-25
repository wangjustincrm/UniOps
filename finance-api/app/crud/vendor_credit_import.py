"""QBO vendor-credit import — a THROWAWAY migration tool.

QuickBooks is being decommissioned. This module exists only to carry the
still-spendable vendor credit balances across before it goes, and should be
deleted with its schema/API/frontend siblings once that has happened. It
deliberately leaves no database schema behind: the vendor mapping travels in
the request body rather than living in a table.

Two rules here are not negotiable, disposable tool or not, because this writes
spendable money into the ledger `payment_execute` pays out of:

1. **Take `balance`, never `total_amt`.** `balance` is the unapplied
   remainder; `total_amt` is the original face value. Importing face value
   would hand vendors credit they already spent inside QBO. The original is
   kept in `notes` for reference only.
2. **Never insert directly.** Everything goes through
   `crud.vendor_credit.create()`, so the sign normalisation, the duplicate
   check and the numbering sequence apply identically to an imported credit
   and a manually uploaded one.

What is NOT imported: the credits QBO has already fully applied
(`balance = 0`) — 443 of the 461 rows in the measured snapshot. They are
history, not balances, and `vendor_credits` is a live ledger rather than an
archive: importing them as spendable would manufacture credit, and importing
them as `exhausted` would demand vendor mappings for dozens of long-dead
vendors purely to satisfy a NOT NULL column. That history stays queryable in
`qbo_vendor_credits`, which lives in this same database and holds the raw QBO
payload including the LinkedTxn proving each application.
"""
import re
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import vendor_credit as vc_crud
from app.models.mirrors import BusinessPartner
from app.models.qbo import SUCCESS, QboSyncRun, QboVendorCredit
from app.models.vendor_credit import AVAILABLE, SOURCE_QBO_IMPORT, VendorCredit
from app.schemas.vendor_credit import VendorCreditCreate

_ZERO = Decimal("0")

# One trailing legal suffix is dropped. Measured against the real dev snapshot:
# lower(strip()) alone matches 5 of the 14 vendors holding importable credit;
# adding punctuation stripping, this suffix list and whitespace collapsing
# takes it to 7 — it is what catches "Alamfoods Inc" -> "Alamfoods Inc." and
# the double-spaced "MSC  Industrial Supply ULC".
_SUFFIXES = {"inc", "ltd", "llc", "ulc", "corp", "corporation", "co", "company", "sec"}


def norm(s: str | None) -> str:
    """Normalise a vendor name for matching. Used for SUGGESTIONS ONLY.

    Nothing in this module acts on a suggestion by itself — see the two-sided
    ambiguity rule in _suggestions() and the mapping requirement in
    run_import(). A normaliser this aggressive will eventually collide two
    genuinely different vendors; that is acceptable precisely because a human
    confirms every mapping.
    """
    if not s:
        return ""
    t = re.sub(r"[.,()]", " ", s.lower())
    t = re.sub(r"\s+", " ", t).strip()
    parts = t.split(" ")
    if len(parts) > 1 and parts[-1] in _SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts).strip()


def _credit_number(row: QboVendorCredit) -> str:
    """QBO permits an empty DocNumber; vendor_credits.vendor_credit_number is
    NOT NULL under a unique index that is not scoped by source. Two
    blank-numbered credits for one vendor would therefore collide with each
    other. Synthesising from the QBO id keeps them distinct and traceable back
    to the source row."""
    doc = (row.doc_number or "").strip()
    return doc if doc else f"QBO-{row.qbo_id}"


def _credit_date(row: QboVendorCredit) -> date:
    try:
        return date.fromisoformat((row.txn_date or "")[:10])
    except ValueError:
        return date.today()


def _importable():
    """The rows this tool will consider: still-spendable, not deleted."""
    return (
        select(QboVendorCredit)
        .where(QboVendorCredit.deleted_at.is_(None),
               QboVendorCredit.balance.is_not(None),
               QboVendorCredit.balance > _ZERO)
    )


async def _suggestions(db: AsyncSession,
                       qbo_names: dict[str, str | None]) -> dict[str, tuple]:
    """Map qbo_vendor_id -> (partner_id, partner_name), omitting anything
    ambiguous on EITHER side.

    Two-sided is the rule POST /qbo/vendor-emails/backfill already uses and it
    matters in both directions: two EPMS partners normalising alike means we
    cannot tell which one is meant, and two QBO vendors normalising alike means
    pointing both at one partner would merge two vendors' credit.
    """
    partners = (await db.execute(
        select(BusinessPartner.id, BusinessPartner.name)
        .where(BusinessPartner.is_supplier.is_(True))
    )).all()

    by_key: dict[str, list] = {}
    for pid, name in partners:
        by_key.setdefault(norm(name), []).append((pid, name))

    qbo_key_counts: dict[str, int] = {}
    for name in qbo_names.values():
        k = norm(name)
        qbo_key_counts[k] = qbo_key_counts.get(k, 0) + 1

    out: dict[str, tuple] = {}
    for qid, name in qbo_names.items():
        key = norm(name)
        if not key or qbo_key_counts.get(key, 0) > 1:
            continue
        hits = by_key.get(key, [])
        if len(hits) == 1:
            out[qid] = hits[0]
    return out


async def _latest_full_reload(db: AsyncSession) -> QboSyncRun | None:
    return (await db.execute(
        select(QboSyncRun)
        .where(QboSyncRun.mode == "full", QboSyncRun.status == SUCCESS,
               QboSyncRun.finished_at.is_not(None))
        .order_by(QboSyncRun.finished_at.desc())
        .limit(1)
    )).scalars().first()


async def _imported_by_source_ref(db: AsyncSession) -> dict[str, VendorCredit]:
    rows = (await db.execute(
        select(VendorCredit).where(VendorCredit.source == SOURCE_QBO_IMPORT,
                                   VendorCredit.source_ref.is_not(None))
    )).scalars().all()
    return {r.source_ref: r for r in rows}


async def list_candidates(db: AsyncSession) -> dict:
    """One entry per QBO vendor still holding spendable credit, plus drift."""
    rows = (await db.execute(_importable())).scalars().all()
    already = await _imported_by_source_ref(db)

    qbo_names = {r.counterparty_id: r.counterparty_name
                 for r in rows if r.counterparty_id}
    suggested = await _suggestions(db, qbo_names)

    grouped: dict[str, dict] = {}
    for r in rows:
        if not r.counterparty_id:
            continue
        g = grouped.setdefault(r.counterparty_id, {
            "qbo_display_name": r.counterparty_name,
            "credit_count": 0,
            "credit_total": _ZERO,
            "currencies": set(),
            "already_imported": 0,
        })
        g["credit_count"] += 1
        g["credit_total"] += Decimal(r.balance)
        g["currencies"].add(r.currency or "CAD")
        if r.qbo_id in already:
            g["already_imported"] += 1

    candidates = []
    for qid, g in grouped.items():
        pid, pname = suggested.get(qid, (None, None))
        candidates.append({
            "qbo_vendor_id": qid,
            "qbo_display_name": g["qbo_display_name"],
            "credit_count": g["credit_count"],
            "credit_total": str(g["credit_total"]),
            # A vendor can hold credit in more than one currency; a credit only
            # ever nets against a payment in its own currency, so this is shown
            # rather than reduced to one value.
            "currencies": sorted(g["currencies"]),
            "suggested_vendor_id": str(pid) if pid else None,
            "suggested_vendor_name": pname,
            "already_imported": g["already_imported"],
        })
    candidates.sort(key=lambda c: (c["qbo_display_name"] or "").lower())

    run = await _latest_full_reload(db)
    return {
        "candidates": candidates,
        "drift": await _drift(db, already),
        "cutover": {
            "sync_run_id": str(run.id) if run else None,
            "finished_at": run.finished_at.isoformat() if run else None,
        },
    }


async def _drift(db: AsyncSession, already: dict[str, VendorCredit]) -> list[dict]:
    """Rows we imported whose QBO balance has since moved.

    Compared against `total_amount`, NOT `remaining_amount`. Our own
    applications decrement `applied`/`remaining` and leave `total` alone, so a
    credit we legitimately spent through EPMS must not surface here — only a
    change made on the QBO side does.

    Reported, never corrected. Our row may already carry applications; silently
    overwriting it would erase them.
    """
    if not already:
        return []
    rows = (await db.execute(
        select(QboVendorCredit).where(QboVendorCredit.qbo_id.in_(list(already)))
    )).scalars().all()

    out = []
    for r in rows:
        ours = already[r.qbo_id]
        qbo_balance = Decimal(r.balance) if r.balance is not None else _ZERO
        if qbo_balance == Decimal(ours.total_amount):
            continue
        out.append({
            "source_ref": r.qbo_id,
            "credit_number": ours.credit_number,
            "vendor_name": ours.vendor_name,
            "imported_total": str(ours.total_amount),
            "qbo_balance": str(qbo_balance),
            "applied_amount": str(ours.applied_amount),
        })
    out.sort(key=lambda d: d["credit_number"])
    return out


async def run_import(db: AsyncSession, *, mapping: dict[str, uuid.UUID],
                     imported_by: uuid.UUID, imported_by_name: str | None = None,
                     dry_run: bool = True) -> dict:
    """Import the mapped vendors' spendable credits.

    Only vendors present in `mapping` are touched — an unmapped vendor is
    counted and reported, never guessed at. A dry run performs the real writes
    inside a savepoint and rolls it back, so the preview exercises the same
    duplicate checks and CHECK constraints the commit will; a preview that
    skipped them could promise rows that then fail.
    """
    rows = (await db.execute(_importable())).scalars().all()
    already = await _imported_by_source_ref(db)
    run = await _latest_full_reload(db)

    names: dict[uuid.UUID, str] = {}
    if mapping:
        for pid, pname in (await db.execute(
            select(BusinessPartner.id, BusinessPartner.name)
            .where(BusinessPartner.id.in_(list(mapping.values())))
        )).all():
            names[pid] = pname

    imported = 0
    skipped_unmapped = 0
    skipped_existing = 0
    skipped_duplicate = 0
    total = _ZERO
    out_rows: list[dict] = []
    duplicates: list[dict] = []

    sp = await db.begin_nested()
    try:
        for r in rows:
            if r.qbo_id in already:
                skipped_existing += 1
                continue
            vendor_id = mapping.get(r.counterparty_id or "")
            if vendor_id is None:
                skipped_unmapped += 1
                continue

            balance = Decimal(r.balance)
            doc_no = _credit_number(r)
            face = r.total_amt if r.total_amt is not None else balance
            currency = r.currency or "CAD"
            payload = VendorCreditCreate(
                vendor_id=vendor_id,
                vendor_name=names.get(vendor_id) or (r.counterparty_name or "Unknown"),
                vendor_credit_number=doc_no,
                credit_date=_credit_date(r),
                currency=currency,
                # balance, not total_amt — see the module docstring. The face
                # value is kept as a note so anyone reconciling against QBO can
                # see why the two figures differ.
                amount=balance,
                tax_amount=_ZERO,
                notes=(f"Imported from QuickBooks vendor credit {r.qbo_id}. "
                       f"Original amount {face} {currency}; "
                       f"unapplied balance at import {balance}."),
            )
            try:
                vc = await vc_crud.create(
                    db, payload=payload,
                    uploaded_by=imported_by, uploaded_by_name=imported_by_name,
                    source=SOURCE_QBO_IMPORT, source_ref=r.qbo_id,
                    opening_balance=True,
                    imported_from_sync_run_id=run.id if run else None,
                    # Skips review: the figure was already reconciled inside
                    # QuickBooks, and AP confirms the mapping here anyway.
                    status=AVAILABLE,
                )
            except vc_crud.DuplicateCredit as exc:
                # This vendor document is already in the ledger from a manual
                # upload. Importing it again would double the vendor's credit.
                skipped_duplicate += 1
                duplicates.append({
                    "qbo_id": r.qbo_id,
                    "vendor_credit_number": doc_no,
                    "existing_credit_number": exc.existing.credit_number,
                })
                continue

            imported += 1
            total += balance
            out_rows.append({
                "qbo_id": r.qbo_id,
                "vendor_credit_number": doc_no,
                "vendor_name": vc.vendor_name,
                "amount": str(balance),
                "currency": currency,
            })

        if dry_run:
            await sp.rollback()
        else:
            await sp.commit()
    except Exception:
        if sp.is_active:
            await sp.rollback()
        raise

    return {
        "dry_run": dry_run,
        "imported": imported,
        "skipped_unmapped": skipped_unmapped,
        "skipped_existing": skipped_existing,
        "skipped_duplicate": skipped_duplicate,
        "total_amount": str(total),
        "rows": out_rows,
        "duplicates": duplicates,
    }
