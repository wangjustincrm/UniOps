"""Persisting a bank reconciliation: statements, advices, match groups, sessions.

The services do the thinking — bank_statement_parse verifies, bank_advice_parse
ties, bank_matching decides — and this module is what writes the answers down and
keeps them consistent afterwards.

Three invariants live here rather than in the ladder, because they are about
stored state rather than about one run:

  - a statement line clears exactly once (uq_bank_recon_match_txn_once), which is
    also what makes re-running the ladder idempotent;
  - a finalized period is frozen: its snapshot is the record, and nothing —
    not a re-import, not an NC re-sync — rewrites it;
  - every ledger reference carries NC's natural key beside the surrogate id, so
    heal_matches() can re-point it after a full nc_sync regenerates every
    journal_voucher_lines row.
"""
import hashlib
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank import MATCHED, UNMATCHED, BankAccount, BankTransaction
from app.models.bank_recon import (
    FINALIZED, IMPORTED, OPEN, SUPERSEDED,
    BankPaymentAdvice, BankPaymentAdviceLine, BankReconciliation, BankReconMatch,
    BankReconMatchBook, BankReconMatchTxn, BankStatement,
)
from app.models.journal_voucher import JournalVoucher, JournalVoucherLine
from app.services import bank_advice_parse, bank_book, bank_matching, bank_statement_parse

log = logging.getLogger(__name__)
ZERO = Decimal("0")


class Frozen(RuntimeError):
    """The period is finalized. Reopen it first."""


def _txn_hash(account_id: uuid.UUID, d: date, amount: Decimal, desc: str, ref: str) -> str:
    """Same shape as crud/bank.py's CSV importer, so a period imported by either
    route dedupes against the other."""
    raw = f"{account_id}|{d.isoformat()}|{amount}|{desc}|{ref}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── statements ─────────────────────────────────────────────────────────────────

async def import_statement_pdf(db: AsyncSession, account: BankAccount, data: bytes,
                               filename: str, actor_id: uuid.UUID) -> dict:
    """Read a statement PDF and store it with its lines.

    An UNVERIFIED statement is still stored. Its verify_errors name the line that
    does not tie, which is the only way a human can fix it; throwing the parse
    away would leave them with "it failed" and nothing to look at. The
    reconciliation is what refuses to use it.
    """
    parsed = bank_statement_parse.parse_statement(data, filename=filename)
    sha = hashlib.sha256(data).hexdigest()

    # A re-import of the same period supersedes the old row. Its transactions are
    # kept and re-pointed by import_hash, so matches made against unchanged lines
    # survive a re-read.
    prior = (await db.execute(
        select(BankStatement).where(
            BankStatement.bank_account_id == account.id,
            BankStatement.period_start == parsed.period_start,
            BankStatement.period_end == parsed.period_end,
            BankStatement.status == IMPORTED)
    )).scalar_one_or_none()
    if prior is not None:
        prior.status = SUPERSEDED

    st = BankStatement(
        bank_account_id=account.id, period_start=parsed.period_start,
        period_end=parsed.period_end, currency=parsed.currency or account.currency,
        opening_balance=parsed.opening_balance, closing_balance=parsed.closing_balance,
        total_debits=parsed.printed_total_debits,
        total_credits=parsed.printed_total_credits,
        debit_count=parsed.printed_debit_count, credit_count=parsed.printed_credit_count,
        statement_account_no=parsed.account_no,
        source_filename=filename, source_sha256=sha,
        parse_method=parsed.parse_method, parse_model=parsed.parse_model,
        parsed_payload=parsed.raw_payload,
        verified=parsed.verified, verify_errors=parsed.verify_errors or None,
        status=IMPORTED, imported_by=actor_id, imported_at=datetime.now(timezone.utc),
        entity_id=account.entity_id,
    )
    db.add(st)
    await db.flush()

    imported = reused = 0
    for ln in parsed.lines:
        h = _txn_hash(account.id, ln.txn_date, ln.amount, ln.description, "")
        existing = (await db.execute(
            select(BankTransaction).where(BankTransaction.import_hash == h)
        )).scalar_one_or_none()
        if existing is not None:
            # Same line, re-read: keep the row (and anything matched to it) and
            # just re-point it at the new statement.
            existing.statement_id = st.id
            existing.running_balance = ln.running_balance
            existing.sort_seq = ln.seq
            reused += 1
            continue
        db.add(BankTransaction(
            bank_account_id=account.id, txn_date=ln.txn_date,
            description=ln.description[:500], amount=ln.amount,
            currency=st.currency, status=UNMATCHED, import_hash=h,
            statement_id=st.id, running_balance=ln.running_balance, sort_seq=ln.seq,
            entity_id=account.entity_id))
        imported += 1
    await db.flush()
    return {"statement_id": str(st.id), "verified": st.verified,
            "verify_errors": st.verify_errors or [],
            "lines": len(parsed.lines), "imported": imported, "reused": reused,
            "superseded": str(prior.id) if prior else None}


# ── payment advices ────────────────────────────────────────────────────────────

async def import_advice_pdf(db: AsyncSession, account: BankAccount, data: bytes,
                            filename: str, actor_id: uuid.UUID) -> list[dict]:
    """Read one payment file. A multi-row bill-payment file becomes several
    advices — the statement sees one line per confirmation number."""
    parsed = bank_advice_parse.parse_advice(data)
    sha = hashlib.sha256(data).hexdigest()
    parts = bank_advice_parse.split_bill_payments(parsed)

    out = []
    for i, part in enumerate(parts):
        # The same file dragged in twice is the same advice; a split file needs a
        # distinct hash per part or the second one collides with the first.
        part_sha = sha if len(parts) == 1 else hashlib.sha256(
            f"{sha}:{part.confirmation_number or i}".encode()).hexdigest()
        existing = (await db.execute(
            select(BankPaymentAdvice).where(
                BankPaymentAdvice.bank_account_id == account.id,
                BankPaymentAdvice.source_sha256 == part_sha)
        )).scalar_one_or_none()
        if existing is not None:
            out.append({"advice_id": str(existing.id), "duplicate": True,
                        "total": str(existing.total), "tie_ok": existing.tie_ok})
            continue

        adv = BankPaymentAdvice(
            bank_account_id=account.id, advice_kind=part.kind,
            advice_date=part.advice_date, currency=part.currency or account.currency,
            client_number=part.client_number,
            confirmation_number=part.confirmation_number,
            total=part.total, line_count=len(part.lines),
            printed_total=part.printed_total, printed_count=part.printed_count,
            tie_ok=part.tie_ok, tie_error=(part.tie_error or None),
            source_filename=filename, source_sha256=part_sha,
            parse_method=part.parse_method, status=IMPORTED,
            imported_by=actor_id, imported_at=datetime.now(timezone.utc),
            entity_id=account.entity_id,
        )
        db.add(adv)
        await db.flush()
        for ln in part.lines:
            db.add(BankPaymentAdviceLine(
                advice_id=adv.id, seq=ln.seq, payee_code=ln.payee_code,
                payee_name=ln.payee_name, currency=ln.currency, amount=ln.amount))
        out.append({"advice_id": str(adv.id), "duplicate": False,
                    "kind": adv.advice_kind, "advice_date": adv.advice_date.isoformat(),
                    "total": str(adv.total), "lines": adv.line_count,
                    "tie_ok": adv.tie_ok, "tie_error": adv.tie_error,
                    "confirmation_number": adv.confirmation_number})
    await db.flush()
    return out


# ── the session ────────────────────────────────────────────────────────────────

async def get_or_open(db: AsyncSession, account: BankAccount,
                      period_start: date, period_end: date,
                      actor_id: uuid.UUID) -> BankReconciliation:
    rec = (await db.execute(
        select(BankReconciliation).where(
            BankReconciliation.bank_account_id == account.id,
            BankReconciliation.period_start == period_start,
            BankReconciliation.period_end == period_end)
    )).scalar_one_or_none()
    if rec is not None:
        return rec
    rec = BankReconciliation(
        bank_account_id=account.id, period_start=period_start, period_end=period_end,
        currency=account.currency, status=OPEN, created_by=actor_id,
        entity_id=account.entity_id)
    db.add(rec)
    await db.flush()
    return rec


async def _period_txns(db: AsyncSession, rec: BankReconciliation) -> list[BankTransaction]:
    return list((await db.execute(
        select(BankTransaction).where(
            BankTransaction.bank_account_id == rec.bank_account_id,
            BankTransaction.txn_date >= rec.period_start,
            BankTransaction.txn_date <= rec.period_end)
        .order_by(BankTransaction.sort_seq, BankTransaction.txn_date)
    )).scalars().all())


async def _period_advices(db: AsyncSession, rec: BankReconciliation,
                          window: int) -> list[BankPaymentAdvice]:
    from datetime import timedelta
    return list((await db.execute(
        select(BankPaymentAdvice).where(
            BankPaymentAdvice.bank_account_id == rec.bank_account_id,
            BankPaymentAdvice.status == IMPORTED,
            BankPaymentAdvice.advice_date >= rec.period_start - timedelta(days=window),
            BankPaymentAdvice.advice_date <= rec.period_end + timedelta(days=window))
    )).scalars().all())


async def _advice_views(db: AsyncSession, advices: list[BankPaymentAdvice]) -> list:
    if not advices:
        return []
    rows = (await db.execute(
        select(BankPaymentAdviceLine)
        .where(BankPaymentAdviceLine.advice_id.in_([a.id for a in advices]))
        .order_by(BankPaymentAdviceLine.seq)
    )).scalars().all()
    by_advice: dict = {}
    for ln in rows:
        by_advice.setdefault(ln.advice_id, []).append(ln)
    return [
        bank_matching.AdviceView(
            id=a.id, kind=a.advice_kind, advice_date=a.advice_date, total=a.total,
            confirmation_number=a.confirmation_number, client_number=a.client_number,
            tie_ok=a.tie_ok,
            lines=[bank_matching.AdviceLineView(id=ln.id, payee_name=ln.payee_name,
                                                amount=ln.amount)
                   for ln in by_advice.get(a.id, [])])
        for a in advices
    ]


async def _claimed(db: AsyncSession, rec: BankReconciliation) -> tuple[set, set]:
    """What is already cleared in this period — bank transaction ids and ledger
    line ids. Re-running the ladder must add to this, never re-decide it."""
    match_ids = (await db.execute(
        select(BankReconMatch.id).where(BankReconMatch.reconciliation_id == rec.id)
    )).scalars().all()
    if not match_ids:
        return set(), set()
    txns = set((await db.execute(
        select(BankReconMatchTxn.bank_transaction_id)
        .where(BankReconMatchTxn.match_id.in_(match_ids))
    )).scalars().all())
    books = set((await db.execute(
        select(BankReconMatchBook.jv_line_id)
        .where(BankReconMatchBook.match_id.in_(match_ids),
               BankReconMatchBook.jv_line_id.is_not(None))
    )).scalars().all())
    return txns, books


async def auto_match(db: AsyncSession, rec: BankReconciliation, account: BankAccount,
                     actor_id: uuid.UUID,
                     window_days: int = bank_matching.DEFAULT_WINDOW_DAYS) -> dict:
    """Run the ladder over everything not already cleared, and persist what
    balances. Idempotent: a second run finds nothing new."""
    if rec.status == FINALIZED:
        raise Frozen("This period is finalized. Reopen it before matching again.")

    period = await bank_book.book_period(db, account, rec.period_start, rec.period_end)
    claimed_txn, claimed_book = await _claimed(db, rec)

    txns = [t for t in await _period_txns(db, rec) if t.id not in claimed_txn]
    advices = await _period_advices(db, rec, window_days)
    used_advice = set((await db.execute(
        select(BankReconMatch.advice_id).where(
            BankReconMatch.reconciliation_id == rec.id,
            BankReconMatch.advice_id.is_not(None))
    )).scalars().all())
    advices = [a for a in advices if a.id not in used_advice]

    book_views = [
        bank_matching.BookLineView(
            id=b.jv_line_id, voucher_date=b.voucher_date, summary=b.summary,
            amount=b.amount, contra_kind=b.contra_kind, jv_number=b.jv_number)
        for b in period.lines if b.jv_line_id not in claimed_book]
    book_by_id = {b.jv_line_id: b for b in period.lines}

    plan = bank_matching.plan(
        [bank_matching.BankTxn(id=t.id, txn_date=t.txn_date, description=t.description,
                               amount=t.amount, currency=t.currency) for t in txns],
        await _advice_views(db, advices), book_views, window_days=window_days)

    txn_by_id = {t.id: t for t in txns}
    for g in plan.groups:
        match = BankReconMatch(
            reconciliation_id=rec.id, method=g.method, amount=g.amount,
            advice_id=g.advice_id, note=g.note, matched_by=actor_id,
            matched_at=datetime.now(timezone.utc))
        db.add(match)
        await db.flush()
        for tid in g.bank_ids:
            db.add(BankReconMatchTxn(match_id=match.id, bank_transaction_id=tid))
            txn_by_id[tid].status = MATCHED
            txn_by_id[tid].matched_at = datetime.now(timezone.utc)
            txn_by_id[tid].matched_by = actor_id
        for bid in g.book_ids:
            b = book_by_id[bid]
            db.add(BankReconMatchBook(
                match_id=match.id, jv_line_id=b.jv_line_id,
                nc_voucher_pk=b.nc_voucher_pk, line_no=b.line_no,
                account_code=b.account_code, amount=b.amount))

    # vendor-level drill-down: which ledger line each advice line turned out to be
    for advice_line_id, book_id in plan.advice_line_links.items():
        ids = book_id if isinstance(book_id, list) else [book_id]
        first = book_by_id.get(ids[0])
        if first is None:
            continue
        line = await db.get(BankPaymentAdviceLine, advice_line_id)
        if line is not None:
            line.matched_jv_line_id = first.jv_line_id
            line.matched_nc_voucher_pk = first.nc_voucher_pk
            line.matched_line_no = first.line_no

    await db.flush()
    summary = await recompute(db, rec, account)
    return {
        "matched_groups": len(plan.groups),
        "bank_lines_cleared": sum(len(g.bank_ids) for g in plan.groups),
        "book_lines_cleared": sum(len(g.book_ids) for g in plan.groups),
        "by_method": {m: sum(1 for g in plan.groups if g.method == m)
                      for m in {g.method for g in plan.groups}},
        "findings": [{"kind": f.kind, "message": f.message,
                      "bank_ids": [str(i) for i in f.bank_ids],
                      "advice_ids": [str(i) for i in f.advice_ids],
                      "book_ids": [str(i) for i in f.book_ids]} for f in plan.findings],
        "summary": summary,
    }


def _summary_from_stored(rec: BankReconciliation, account) -> dict:
    """What was signed off, read back from the row — never recalculated.

    Shapes identically to recompute()'s return so every caller keeps working; the
    two line counts come from the snapshot, which is the only place they were
    ever kept after sign-off.
    """
    snap = rec.snapshot or {}
    stored = snap.get("summary") if isinstance(snap, dict) else None
    if isinstance(stored, dict) and stored.get("difference") is not None:
        return stored
    opening_difference = (rec.statement_opening or ZERO) - (rec.book_opening or ZERO)
    return {
        "statement_opening": str(rec.statement_opening) if rec.statement_opening is not None else None,
        "statement_closing": str(rec.statement_closing) if rec.statement_closing is not None else None,
        "statement_verified": True,      # it could not have been signed off otherwise
        "book_opening": str(rec.book_opening), "book_closing": str(rec.book_closing),
        "cleared_debit_total": str(rec.cleared_debit_total),
        "cleared_credit_total": str(rec.cleared_credit_total),
        "cleared_debit_count": rec.cleared_debit_count,
        "cleared_credit_count": rec.cleared_credit_count,
        "outstanding_bank_lines": 0, "outstanding_book_lines": 0,
        "difference": str(rec.difference),
        "opening_difference": str(opening_difference),
        "movement_difference": str(rec.difference - opening_difference),
        "bank_account": account.name,
        "status": rec.status,
    }


async def recompute(db: AsyncSession, rec: BankReconciliation,
                    account: BankAccount) -> dict:
    """Refresh the session's totals from what is actually stored.

    `difference` is the number the whole feature exists to drive to zero:
    statement closing minus (book opening + everything the ledger did this
    period). It is NOT computed from the cleared lines — that would define away
    the discrepancy it is supposed to expose.

    ★ A finalized period is NOT recomputed. Sign-off freezes the snapshot for the
    report, but every read still ran this and overwrote the stored figures, so a
    signed-off July went from 0.00 to −255,207.81 the moment August was imported
    (production, 2026-09-24). "Frozen" that only protects the PDF is not frozen:
    what the auditor is shown on screen has to be what was signed.
    """
    if rec.status == FINALIZED:
        return _summary_from_stored(rec, account)
    period = await bank_book.book_period(db, account, rec.period_start, rec.period_end)

    # The statement for THIS period is the one that overlaps it most, not the
    # latest that touches it. A bank dates each statement from the previous
    # closing date, so consecutive months share an endpoint: August runs
    # 07-31 → 08-31 and so "overlaps" a July reconciliation by exactly one day.
    # Ordering by period_end desc then handed July the August statement, and the
    # moment August was imported a July period that had been signed off at 0.00
    # started reporting a difference of −255,207.81 (production, 2026-09-24).
    overlapping = (await db.execute(
        select(BankStatement).where(
            BankStatement.bank_account_id == account.id,
            BankStatement.status == IMPORTED,
            BankStatement.period_start <= rec.period_end,
            BankStatement.period_end >= rec.period_start)
    )).scalars().all()

    def _overlap_days(st: BankStatement) -> int:
        start = max(st.period_start, rec.period_start)
        end = min(st.period_end, rec.period_end)
        return (end - start).days

    # Tie-break on the later period_end only among equally-overlapping ones, so a
    # genuine re-import of the same month still wins over the original.
    statement = max(overlapping, key=lambda st: (_overlap_days(st), st.period_end),
                    default=None)

    claimed_txn, claimed_book = await _claimed(db, rec)
    txns = await _period_txns(db, rec)
    cleared = [t for t in txns if t.id in claimed_txn]
    outstanding = [t for t in txns if t.id not in claimed_txn]
    uncleared_book = [b for b in period.lines if b.jv_line_id not in claimed_book]

    rec.statement_id = statement.id if statement else None
    rec.statement_opening = statement.opening_balance if statement else None
    rec.statement_closing = statement.closing_balance if statement else None
    rec.book_opening = period.opening
    rec.book_closing = period.closing
    rec.cleared_debit_total = -sum((t.amount for t in cleared if t.amount < ZERO), ZERO)
    rec.cleared_credit_total = sum((t.amount for t in cleared if t.amount > ZERO), ZERO)
    rec.cleared_debit_count = sum(1 for t in cleared if t.amount < ZERO)
    rec.cleared_credit_count = sum(1 for t in cleared if t.amount > ZERO)
    rec.outstanding_debit_total = -sum((t.amount for t in outstanding if t.amount < ZERO), ZERO)
    rec.outstanding_credit_total = sum((t.amount for t in outstanding if t.amount > ZERO), ZERO)
    rec.difference = ((statement.closing_balance if statement else ZERO) - period.closing)

    # Split the difference into where it came from. They sum to `difference`, and
    # which one is non-zero says what to do about it:
    #   opening  — the two sides disagreed BEFORE this period started. Nothing you
    #              match inside it can move this number; the carry-in is wrong, or
    #              a previous period was never reconciled.
    #   movement — the period's own debits and credits disagree. That is what
    #              matching is for.
    # July 2026 on RBC read as "the ledger does not reconcile" when in fact the
    # movement matched to the cent and the entire gap was carry-in — and with all
    # 260 lines ticked, the only reading left was "something is unmatched".
    stmt_opening = statement.opening_balance if statement else ZERO
    opening_difference = stmt_opening - period.opening
    movement_difference = rec.difference - opening_difference

    await db.flush()
    return {
        "statement_opening": str(rec.statement_opening) if statement else None,
        "statement_closing": str(rec.statement_closing) if statement else None,
        "statement_verified": bool(statement and statement.verified),
        "book_opening": str(rec.book_opening), "book_closing": str(rec.book_closing),
        "cleared_debit_total": str(rec.cleared_debit_total),
        "cleared_credit_total": str(rec.cleared_credit_total),
        "cleared_debit_count": rec.cleared_debit_count,
        "cleared_credit_count": rec.cleared_credit_count,
        "outstanding_bank_lines": len(outstanding),
        "outstanding_book_lines": len(uncleared_book),
        "difference": str(rec.difference),
        "opening_difference": str(opening_difference),
        "movement_difference": str(movement_difference),
        "bank_account": period.bank_account_label,
        "status": rec.status,
        # Everything above is in this currency. On a non-CAD account the FX
        # revaluation / CAD-only lines are outside it, counted here in CAD.
        "currency": period.currency,
        "base_only_count": len(period.base_only or []),
        "base_only_local_total": str(period.base_only_local_total),
    }


async def manual_group(db: AsyncSession, rec: BankReconciliation, account: BankAccount,
                       txn_ids: list[uuid.UUID], jv_line_ids: list[uuid.UUID],
                       actor_id: uuid.UUID, note: str | None = None) -> BankReconMatch:
    """A human builds the group the ladder would not.

    Still balanced: a person may know WHICH lines go together, but "these add up"
    is arithmetic and is not a judgement call. An unbalanced group would put a
    difference into a reconciliation while showing it as cleared.
    """
    if rec.status == FINALIZED:
        raise Frozen("This period is finalized. Reopen it before changing matches.")
    if not txn_ids or not jv_line_ids:
        raise ValueError("A match needs at least one statement line and one ledger line.")

    txns = list((await db.execute(
        select(BankTransaction).where(BankTransaction.id.in_(txn_ids))
    )).scalars().all())
    if len(txns) != len(set(txn_ids)):
        raise LookupError("One of those statement lines no longer exists.")

    period = await bank_book.book_period(db, account, rec.period_start, rec.period_end)
    book_by_id = {b.jv_line_id: b for b in period.lines}
    missing = [i for i in jv_line_ids if i not in book_by_id]
    if missing:
        raise LookupError(
            "Those ledger lines are not on this bank account in this period. A line "
            "can only clear against the account it was posted to.")

    bank_total = sum((t.amount for t in txns), ZERO)
    book_total = sum((book_by_id[i].amount for i in jv_line_ids), ZERO)
    if bank_total != book_total:
        raise ValueError(
            f"These do not balance: the statement side is {bank_total} and the ledger "
            f"side is {book_total}, a difference of {bank_total - book_total}. Add the "
            f"missing line, or clear them as separate matches.")

    claimed_txn, claimed_book = await _claimed(db, rec)
    if set(txn_ids) & claimed_txn:
        raise ValueError("One of those statement lines is already cleared.")
    if set(jv_line_ids) & claimed_book:
        raise ValueError("One of those ledger lines is already cleared.")

    match = BankReconMatch(
        reconciliation_id=rec.id, method=bank_matching.M_MANUAL, amount=bank_total, note=note, matched_by=actor_id,
        matched_at=datetime.now(timezone.utc))
    db.add(match)
    await db.flush()
    for t in txns:
        db.add(BankReconMatchTxn(match_id=match.id, bank_transaction_id=t.id))
        t.status = MATCHED
        t.matched_at = datetime.now(timezone.utc)
        t.matched_by = actor_id
    for i in jv_line_ids:
        b = book_by_id[i]
        db.add(BankReconMatchBook(
            match_id=match.id, jv_line_id=b.jv_line_id, nc_voucher_pk=b.nc_voucher_pk,
            line_no=b.line_no, account_code=b.account_code, amount=b.amount))
    await db.flush()
    return match


async def unmatch(db: AsyncSession, rec: BankReconciliation, match_id: uuid.UUID) -> None:
    if rec.status == FINALIZED:
        raise Frozen("This period is finalized. Reopen it before changing matches.")
    match = await db.get(BankReconMatch, match_id)
    if match is None or match.reconciliation_id != rec.id:
        raise LookupError("That match is not part of this reconciliation.")
    txn_ids = (await db.execute(
        select(BankReconMatchTxn.bank_transaction_id)
        .where(BankReconMatchTxn.match_id == match.id))).scalars().all()
    for t in (await db.execute(
            select(BankTransaction).where(BankTransaction.id.in_(txn_ids)))).scalars():
        t.status = UNMATCHED
        t.matched_at = None
        t.matched_by = None
    # advice lines drilled through this group lose their link too
    book_ids = (await db.execute(
        select(BankReconMatchBook.jv_line_id)
        .where(BankReconMatchBook.match_id == match.id))).scalars().all()
    if match.advice_id:
        for ln in (await db.execute(
                select(BankPaymentAdviceLine).where(
                    BankPaymentAdviceLine.advice_id == match.advice_id))).scalars():
            if ln.matched_jv_line_id in book_ids:
                ln.matched_jv_line_id = None
                ln.matched_nc_voucher_pk = None
                ln.matched_line_no = None
    await db.delete(match)
    await db.flush()


async def finalize(db: AsyncSession, rec: BankReconciliation, account: BankAccount,
                   actor_id: uuid.UUID, snapshot: dict) -> None:
    """Sign the period off. Refuses on a non-zero difference or an unverified
    statement — those are the two ways a reconciliation can look done and not be."""
    summary = await recompute(db, rec, account)
    if rec.difference != ZERO:
        # Name WHICH half is off. The old wording gave two closing balances and a
        # difference, and a reader whose lines were all matched could only read it
        # as "something is still unmatched" — which was not what it meant and not
        # what was wrong (user, 2026-09-23).
        opening_gap = Decimal(summary["opening_difference"])
        movement_gap = Decimal(summary["movement_difference"])
        if opening_gap and not movement_gap:
            why = (f"the period's own movements agree to the cent, but the two sides "
                   f"start {opening_gap} apart. Nothing you match inside this period "
                   f"can change that — the opening balance is wrong, or an earlier "
                   f"period was never reconciled.")
        elif movement_gap and not opening_gap:
            why = (f"the opening balances agree, and this period's movements are "
                   f"{movement_gap} apart. That is what matching is for.")
        else:
            why = (f"{opening_gap} of it was already there at the opening and "
                   f"{movement_gap} arose inside the period.")
        raise ValueError(
            f"This period does not reconcile by {rec.difference}: {why} "
            f"(statement closes at {rec.statement_closing}, ledger at "
            f"{rec.book_closing}.)")
    if not summary.get("statement_verified"):
        raise ValueError(
            "The statement for this period has not been verified against its own "
            "printed totals, so it cannot be signed off. Re-import it.")
    rec.status = FINALIZED
    rec.finalized_by = actor_id
    rec.finalized_at = datetime.now(timezone.utc)
    rec.snapshot = snapshot
    await db.flush()


async def reopen(db: AsyncSession, rec: BankReconciliation, actor_id: uuid.UUID) -> None:
    """Unfreeze a signed-off period. The snapshot stays: what was signed off is
    what was signed off, and a later version does not erase it."""
    rec.status = OPEN
    await db.flush()


# ── surviving a full nc_sync ───────────────────────────────────────────────────

async def heal_matches(db: AsyncSession) -> dict:
    """Re-point ledger references orphaned by a full nc_sync.

    That sync deletes every nc-sourced voucher and regenerates the line ids; the
    SET NULL foreign keys mean the match rows survive with a null jv_line_id, and
    NC's natural key (voucher pk, line number) is what finds the new row.

    Finalized periods are skipped on purpose — their snapshot is the record, and
    a signed-off reconciliation must not silently acquire different ledger rows.
    """
    orphans = list((await db.execute(
        select(BankReconMatchBook)
        .join(BankReconMatch, BankReconMatch.id == BankReconMatchBook.match_id)
        .join(BankReconciliation,
              BankReconciliation.id == BankReconMatch.reconciliation_id)
        .where(BankReconMatchBook.jv_line_id.is_(None),
               BankReconMatchBook.nc_voucher_pk.is_not(None),
               BankReconciliation.status != FINALIZED)
    )).scalars().all())
    if not orphans:
        return {"orphans": 0, "healed": 0, "unresolved": 0}

    pks = {o.nc_voucher_pk for o in orphans}
    rows = (await db.execute(
        select(JournalVoucher.nc_source_pk, JournalVoucherLine.line_no,
               JournalVoucherLine.id)
        .join(JournalVoucherLine, JournalVoucherLine.jv_id == JournalVoucher.id)
        .where(JournalVoucher.nc_source_pk.in_(pks))
    )).all()
    index = {(pk, no): lid for pk, no, lid in rows}

    healed = 0
    for o in orphans:
        lid = index.get((o.nc_voucher_pk, o.line_no))
        if lid is not None:
            o.jv_line_id = lid
            healed += 1
    # advice lines carry the same natural key and the same SET NULL exposure
    adv_orphans = list((await db.execute(
        select(BankPaymentAdviceLine).where(
            BankPaymentAdviceLine.matched_jv_line_id.is_(None),
            BankPaymentAdviceLine.matched_nc_voucher_pk.is_not(None))
    )).scalars().all())
    adv_pks = {a.matched_nc_voucher_pk for a in adv_orphans}
    if adv_pks:
        rows2 = (await db.execute(
            select(JournalVoucher.nc_source_pk, JournalVoucherLine.line_no,
                   JournalVoucherLine.id)
            .join(JournalVoucherLine, JournalVoucherLine.jv_id == JournalVoucher.id)
            .where(JournalVoucher.nc_source_pk.in_(adv_pks))
        )).all()
        index2 = {(pk, no): lid for pk, no, lid in rows2}
        for a in adv_orphans:
            lid = index2.get((a.matched_nc_voucher_pk, a.matched_line_no))
            if lid is not None:
                a.matched_jv_line_id = lid
    await db.flush()
    unresolved = len(orphans) - healed
    if unresolved:
        log.warning(
            "bank reconciliation: %d matched ledger lines could not be re-pointed after "
            "an NC re-sync — the voucher or line no longer exists in NC", unresolved)
    return {"orphans": len(orphans), "healed": healed, "unresolved": unresolved}
