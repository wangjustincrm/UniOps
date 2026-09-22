"""Bank Reconciliation v2 API.

Mounted alongside the v1 /bank router rather than replacing it: the CSV import,
the account CRUD and the FX rates there are all still current. What v2 replaces
is the MATCHING model, and those endpoints live here.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.coa import _require_manage
from app.core.deps import BearerToken, CurrentUser
from app.crud import bank_recon as crud
from app.db.base import get_db
from app.models.bank import BankAccount, BankTransaction
from app.models.bank_recon import (
    FINALIZED, IMPORTED, BankPaymentAdvice, BankPaymentAdviceLine, BankReconciliation,
    BankReconMatch, BankReconMatchBook, BankReconMatchTxn, BankStatement,
)
from app.models.nc_bank_account import NcBankAccount
from app.services import bank_book, bank_files, bank_matching, bank_recon_report
from app.services.bank_advice_parse import AdviceUnparseable
from app.services.bank_statement_parse import StatementUnparseable

router = APIRouter(prefix="/bank-recon", tags=["bank-reconciliation-v2"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


async def _account(db: AsyncSession, account_id: uuid.UUID) -> BankAccount:
    acct = (await db.execute(
        select(BankAccount).where(BankAccount.id == account_id))).scalar_one_or_none()
    if acct is None:
        raise HTTPException(status_code=404, detail="Bank account not found")
    return acct


async def _recon(db: AsyncSession, recon_id: uuid.UUID) -> BankReconciliation:
    rec = await db.get(BankReconciliation, recon_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    return rec


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{file.filename} is larger than {MAX_UPLOAD_BYTES // (1024*1024)} MB.")
    return data


def _actor(user: dict) -> uuid.UUID:
    return uuid.UUID(user["sub"])


# ── NC bank accounts, for wiring an account up ─────────────────────────────────

@router.get("/nc-accounts")
async def nc_accounts(_: CurrentUser, db: AsyncSession = Depends(get_db)):
    """The bank accounts NC's 100201 actually carries, for the account mapping.

    Empty until a full nc_sync has run with the bank-account auxiliary decoding
    in place (migration 0036) — and it says so, because an empty picker with no
    explanation reads as a broken page.
    """
    rows = (await db.execute(
        select(NcBankAccount).order_by(NcBankAccount.code))).scalars().all()
    return {
        "accounts": [{"code": r.code, "name": r.name, "acc_num": r.acc_num,
                      "bank_name": r.bank_name, "currency": r.currency,
                      "label": r.label} for r in rows],
        "hint": None if rows else (
            "No NC bank accounts are known yet. They arrive with a full NC sync — "
            "until then a reconciliation has no ledger side to compare against."),
    }


# ── statements ─────────────────────────────────────────────────────────────────

@router.post("/{account_id}/statements")
async def import_statement(account_id: uuid.UUID, user: CurrentUser, token: BearerToken,
                           file: UploadFile = File(...),
                           db: AsyncSession = Depends(get_db)):
    """Import a statement PDF. Stored even when it does not verify — the errors
    name the line to fix, and the reconciliation is what refuses to use it."""
    await _require_manage(db, user)
    acct = await _account(db, account_id)
    data = await _read_upload(file)
    try:
        result = await crud.import_statement_pdf(
            db, acct, data, file.filename or "statement.pdf", _actor(user))
    except StatementUnparseable as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:                      # AI outage / billing limit
        raise HTTPException(status_code=503, detail=str(exc))

    key, warning = await bank_files.store(
        data, file.filename or "statement.pdf", "application/pdf",
        bank_files.DOC_STATEMENT, uuid.UUID(result["statement_id"]), token)
    if key:
        st = await db.get(BankStatement, uuid.UUID(result["statement_id"]))
        st.source_storage_key = key
    result["retention_warning"] = warning
    await db.commit()
    return result


@router.get("/{account_id}/statements")
async def list_statements(account_id: uuid.UUID, _: CurrentUser,
                          db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(BankStatement).where(BankStatement.bank_account_id == account_id)
        .order_by(BankStatement.period_end.desc())
    )).scalars().all()
    return [{"id": str(r.id), "period_start": r.period_start.isoformat(),
             "period_end": r.period_end.isoformat(), "status": r.status,
             "opening_balance": str(r.opening_balance),
             "closing_balance": str(r.closing_balance),
             "verified": r.verified, "verify_errors": r.verify_errors or [],
             "source_filename": r.source_filename,
             "has_document": r.source_storage_key is not None,
             "parse_method": r.parse_method} for r in rows]


# ── payment advices ────────────────────────────────────────────────────────────

@router.post("/{account_id}/advices")
async def import_advices(account_id: uuid.UUID, user: CurrentUser, token: BearerToken,
                         files: list[UploadFile] = File(...),
                         db: AsyncSession = Depends(get_db)):
    """Import one or many payment files at once — a month is ~20 of them, and
    making finance upload them one by one is how they end up not uploading them."""
    await _require_manage(db, user)
    acct = await _account(db, account_id)
    out: list[dict] = []
    for f in files:
        data = await _read_upload(f)
        try:
            results = await crud.import_advice_pdf(
                db, acct, data, f.filename or "advice.pdf", _actor(user))
        except AdviceUnparseable as exc:
            out.append({"filename": f.filename, "error": str(exc)})
            continue
        for r in results:
            r["filename"] = f.filename
            if not r.get("duplicate"):
                key, warning = await bank_files.store(
                    data, f.filename or "advice.pdf", "application/pdf",
                    bank_files.DOC_ADVICE, uuid.UUID(r["advice_id"]), token)
                if key:
                    adv = await db.get(BankPaymentAdvice, uuid.UUID(r["advice_id"]))
                    adv.source_storage_key = key
                if warning:
                    r["retention_warning"] = warning
            out.append(r)
    await db.commit()
    return {"results": out,
            "imported": sum(1 for r in out if r.get("advice_id") and not r.get("duplicate")),
            "duplicates": sum(1 for r in out if r.get("duplicate")),
            "failed": sum(1 for r in out if r.get("error")),
            "not_tied": [r for r in out if r.get("tie_ok") is False]}


@router.get("/{account_id}/advices")
async def list_advices(account_id: uuid.UUID, _: CurrentUser,
                       date_from: date | None = Query(default=None),
                       date_to: date | None = Query(default=None),
                       db: AsyncSession = Depends(get_db)):
    q = select(BankPaymentAdvice).where(
        BankPaymentAdvice.bank_account_id == account_id,
        BankPaymentAdvice.status == IMPORTED)
    if date_from:
        q = q.where(BankPaymentAdvice.advice_date >= date_from)
    if date_to:
        q = q.where(BankPaymentAdvice.advice_date <= date_to)
    rows = (await db.execute(q.order_by(BankPaymentAdvice.advice_date))).scalars().all()
    return [{"id": str(r.id), "kind": r.advice_kind,
             "advice_date": r.advice_date.isoformat(), "total": str(r.total),
             "line_count": r.line_count, "tie_ok": r.tie_ok, "tie_error": r.tie_error,
             "confirmation_number": r.confirmation_number,
             "client_number": r.client_number, "source_filename": r.source_filename,
             "has_document": r.source_storage_key is not None} for r in rows]


@router.get("/advices/{advice_id}/lines")
async def advice_lines(advice_id: uuid.UUID, _: CurrentUser,
                       db: AsyncSession = Depends(get_db)):
    """The vendor breakdown behind one merged statement line — the thing finance
    opens the payment-file PDF for today."""
    rows = (await db.execute(
        select(BankPaymentAdviceLine).where(BankPaymentAdviceLine.advice_id == advice_id)
        .order_by(BankPaymentAdviceLine.seq))).scalars().all()
    return [{"id": str(r.id), "seq": r.seq, "payee_code": r.payee_code,
             "payee_name": r.payee_name, "amount": str(r.amount),
             "currency": r.currency,
             "matched_jv_line_id": str(r.matched_jv_line_id) if r.matched_jv_line_id else None}
            for r in rows]


# ── the book side ──────────────────────────────────────────────────────────────

@router.get("/{account_id}/book")
async def book_side(account_id: uuid.UUID, _: CurrentUser,
                    date_from: date = Query(...), date_to: date = Query(...),
                    db: AsyncSession = Depends(get_db)):
    """NC's ledger for this bank account, the way NC's own drill-down shows it."""
    acct = await _account(db, account_id)
    try:
        period = await bank_book.book_period(db, acct, date_from, date_to)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    counterparts = await bank_book.transfer_counterparts(db, period.lines)
    return {
        "bank_account": period.bank_account_label,
        "opening": str(period.opening), "closing": str(period.closing),
        "total_debit": str(period.total_debit), "total_credit": str(period.total_credit),
        "lines": [{
            "jv_line_id": str(b.jv_line_id), "jv_number": b.jv_number,
            "voucher_date": b.voucher_date.isoformat(), "line_no": b.line_no,
            "summary": b.summary, "currency": b.currency,
            "debit": str(b.debit), "credit": str(b.credit), "amount": str(b.amount),
            "contra_codes": b.contra_codes, "contra_names": b.contra_names,
            "contra_kind": b.contra_kind,
            "contra_label": bank_book.KIND_LABELS.get(b.contra_kind, b.contra_kind),
            "partner_name": b.partner_name,
            "transfer_counterpart": counterparts.get(b.jv_line_id),
        } for b in period.lines],
    }


# ── the session ────────────────────────────────────────────────────────────────

class OpenIn(BaseModel):
    period_start: date
    period_end: date


@router.post("/{account_id}/reconciliations")
async def open_period(account_id: uuid.UUID, body: OpenIn, user: CurrentUser,
                      db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    acct = await _account(db, account_id)
    if body.period_end < body.period_start:
        raise HTTPException(status_code=422, detail="The period ends before it starts.")
    rec = await crud.get_or_open(db, acct, body.period_start, body.period_end, _actor(user))
    try:
        summary = await crud.recompute(db, rec, acct)
    except LookupError as exc:
        await db.commit()
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return {"id": str(rec.id), "status": rec.status, "summary": summary}


@router.get("/{account_id}/reconciliations")
async def list_periods(account_id: uuid.UUID, _: CurrentUser,
                       db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(BankReconciliation)
        .where(BankReconciliation.bank_account_id == account_id)
        .order_by(BankReconciliation.period_end.desc()))).scalars().all()
    return [{"id": str(r.id), "period_start": r.period_start.isoformat(),
             "period_end": r.period_end.isoformat(), "status": r.status,
             "difference": str(r.difference),
             "statement_closing": str(r.statement_closing) if r.statement_closing else None,
             "book_closing": str(r.book_closing) if r.book_closing else None,
             "finalized_at": r.finalized_at.isoformat() if r.finalized_at else None,
             "has_report": r.report_storage_key is not None} for r in rows]


@router.get("/reconciliations/{recon_id}")
async def get_period(recon_id: uuid.UUID, _: CurrentUser,
                     db: AsyncSession = Depends(get_db)):
    """The workbench payload: both sides, the groups, and what is still open."""
    rec = await _recon(db, recon_id)
    acct = await _account(db, rec.bank_account_id)
    try:
        summary = await crud.recompute(db, rec, acct)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()

    txns = await crud._period_txns(db, rec)
    claimed_txn, claimed_book = await crud._claimed(db, rec)
    period = await bank_book.book_period(db, acct, rec.period_start, rec.period_end)

    matches = (await db.execute(
        select(BankReconMatch).where(BankReconMatch.reconciliation_id == rec.id)
        .order_by(BankReconMatch.matched_at))).scalars().all()
    mt = {}
    for row in (await db.execute(
            select(BankReconMatchTxn).where(
                BankReconMatchTxn.match_id.in_([m.id for m in matches] or [uuid.uuid4()])))
    ).scalars():
        mt.setdefault(row.match_id, []).append(str(row.bank_transaction_id))
    mb = {}
    for row in (await db.execute(
            select(BankReconMatchBook).where(
                BankReconMatchBook.match_id.in_([m.id for m in matches] or [uuid.uuid4()])))
    ).scalars():
        mb.setdefault(row.match_id, []).append(
            {"jv_line_id": str(row.jv_line_id) if row.jv_line_id else None,
             "nc_voucher_pk": row.nc_voucher_pk, "line_no": row.line_no,
             "amount": str(row.amount)})

    return {
        "id": str(rec.id), "status": rec.status,
        "period_start": rec.period_start.isoformat(),
        "period_end": rec.period_end.isoformat(),
        "currency": rec.currency, "summary": summary,
        "bank_lines": [{
            "id": str(t.id), "txn_date": t.txn_date.isoformat(),
            "description": t.description, "amount": str(t.amount),
            "running_balance": str(t.running_balance) if t.running_balance else None,
            "sort_seq": t.sort_seq, "cleared": t.id in claimed_txn} for t in txns],
        "book_lines": [{
            "jv_line_id": str(b.jv_line_id), "jv_number": b.jv_number,
            "voucher_date": b.voucher_date.isoformat(), "summary": b.summary,
            "amount": str(b.amount), "contra_kind": b.contra_kind,
            "contra_label": bank_book.KIND_LABELS.get(b.contra_kind, b.contra_kind),
            "contra_codes": b.contra_codes,
            "cleared": b.jv_line_id in claimed_book} for b in period.lines],
        "matches": [{
            "id": str(m.id), "method": m.method, "amount": str(m.amount),
            "note": m.note, "advice_id": str(m.advice_id) if m.advice_id else None,
            "bank_ids": mt.get(m.id, []), "book_lines": mb.get(m.id, [])}
            for m in matches],
    }


@router.post("/reconciliations/{recon_id}/auto-match")
async def auto_match(recon_id: uuid.UUID, user: CurrentUser,
                     window_days: int = Query(default=bank_matching.DEFAULT_WINDOW_DAYS,
                                              ge=0, le=60),
                     db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    rec = await _recon(db, recon_id)
    acct = await _account(db, rec.bank_account_id)
    try:
        result = await crud.auto_match(db, rec, acct, _actor(user), window_days=window_days)
    except crud.Frozen as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return result


class MatchIn(BaseModel):
    bank_transaction_ids: list[uuid.UUID] = Field(min_length=1)
    jv_line_ids: list[uuid.UUID] = Field(min_length=1)
    note: str | None = None


@router.post("/reconciliations/{recon_id}/matches")
async def create_match(recon_id: uuid.UUID, body: MatchIn, user: CurrentUser,
                       db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    rec = await _recon(db, recon_id)
    acct = await _account(db, rec.bank_account_id)
    try:
        match = await crud.manual_group(db, rec, acct, body.bank_transaction_ids,
                                        body.jv_line_ids, _actor(user), body.note)
        summary = await crud.recompute(db, rec, acct)
    except crud.Frozen as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return {"id": str(match.id), "amount": str(match.amount), "summary": summary}


@router.delete("/reconciliations/{recon_id}/matches/{match_id}")
async def delete_match(recon_id: uuid.UUID, match_id: uuid.UUID, user: CurrentUser,
                       db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    rec = await _recon(db, recon_id)
    acct = await _account(db, rec.bank_account_id)
    try:
        await crud.unmatch(db, rec, match_id)
        summary = await crud.recompute(db, rec, acct)
    except crud.Frozen as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await db.commit()
    return {"ok": True, "summary": summary}


@router.get("/reconciliations/{recon_id}/candidates/{txn_id}")
async def candidates(recon_id: uuid.UUID, txn_id: uuid.UUID, _: CurrentUser,
                     window_days: int = Query(default=bank_matching.DEFAULT_WINDOW_DAYS,
                                              ge=0, le=60),
                     db: AsyncSession = Depends(get_db)):
    """Ranked ledger lines for a statement line a human has to clear by hand."""
    rec = await _recon(db, recon_id)
    acct = await _account(db, rec.bank_account_id)
    txn = await db.get(BankTransaction, txn_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="Statement line not found")
    period = await bank_book.book_period(db, acct, rec.period_start, rec.period_end)
    _claimed_txn, claimed_book = await crud._claimed(db, rec)
    views = [bank_matching.BookLineView(
        id=b.jv_line_id, voucher_date=b.voucher_date, summary=b.summary,
        amount=b.amount, contra_kind=b.contra_kind, jv_number=b.jv_number)
        for b in period.lines]
    got = bank_matching.candidates_for(
        bank_matching.BankTxn(id=txn.id, txn_date=txn.txn_date,
                              description=txn.description, amount=txn.amount),
        views, claimed_book, window_days=window_days)
    return [{"jv_line_id": str(b.id), "jv_number": b.jv_number,
             "voucher_date": b.voucher_date.isoformat(), "summary": b.summary,
             "amount": str(b.amount), "contra_kind": b.contra_kind,
             "exact": b.amount == txn.amount} for b in got]


# ── sign-off and the report ────────────────────────────────────────────────────

def _rows_for_report(bank_lines, book_lines, claimed_txn, claimed_book):
    cleared_debits, cleared_credits, out_bank, out_book = [], [], [], []
    for t in bank_lines:
        row = {"date": t.txn_date.isoformat(), "type": "Statement line", "ref": "",
               "payee": t.description, "amount": str(abs(t.amount))}
        if t.id in claimed_txn:
            (cleared_debits if t.amount < 0 else cleared_credits).append(row)
        else:
            out_bank.append({**row, "amount": str(t.amount)})
    for b in book_lines:
        if b.jv_line_id not in claimed_book:
            out_book.append({"date": b.voucher_date.isoformat(),
                             "type": bank_book.KIND_LABELS.get(b.contra_kind, ""),
                             "ref": b.jv_number, "payee": b.summary or "",
                             "amount": str(b.amount)})
    return cleared_debits, cleared_credits, out_bank, out_book


async def _snapshot(db: AsyncSession, rec: BankReconciliation, acct: BankAccount,
                    prepared_by: str) -> dict:
    """Build the report payload from the LEDGER side for the cleared details.

    The cleared lists are per ledger line, not per statement line, because that
    is what the QuickBooks report shows and what an auditor checks: 253 payments
    against 30 bank lines. Showing the 30 would hide the 253.
    """
    summary = await crud.recompute(db, rec, acct)
    txns = await crud._period_txns(db, rec)
    claimed_txn, claimed_book = await crud._claimed(db, rec)
    period = await bank_book.book_period(db, acct, rec.period_start, rec.period_end)

    cleared_payments, cleared_deposits = [], []
    for b in period.lines:
        if b.jv_line_id not in claimed_book:
            continue
        row = {"date": b.voucher_date.isoformat(),
               "type": bank_book.KIND_LABELS.get(b.contra_kind, ""),
               "ref": b.jv_number, "payee": b.summary or (b.partner_name or ""),
               "amount": str(abs(b.amount))}
        (cleared_payments if b.amount < 0 else cleared_deposits).append(row)

    _cd, _cc, out_bank, out_book = _rows_for_report(
        txns, period.lines, claimed_txn, claimed_book)
    return bank_recon_report.build_snapshot(
        rec, period.bank_account_label, summary, cleared_payments, cleared_deposits,
        out_bank, out_book, prepared_by)


@router.get("/reconciliations/{recon_id}/report")
async def report(recon_id: uuid.UUID, user: CurrentUser,
                 fmt: str = Query(default="pdf", pattern="^(pdf|xlsx)$"),
                 db: AsyncSession = Depends(get_db)):
    rec = await _recon(db, recon_id)
    acct = await _account(db, rec.bank_account_id)
    # A finalized period reports from its snapshot — what was signed off is what
    # is printed, whatever the ledger has done since.
    snapshot = rec.snapshot or await _snapshot(db, rec, acct, user.get("name") or "")
    await db.commit()
    on = (rec.finalized_at.date() if rec.finalized_at else datetime.now(timezone.utc).date())
    by = snapshot.get("prepared_by") or user.get("name") or ""
    stamp = f"{acct.name}-{rec.period_end.isoformat()}".replace(" ", "_")
    if fmt == "xlsx":
        data = bank_recon_report.render_xlsx(snapshot, on, by)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        name = f"bank-reconciliation-{stamp}.xlsx"
    else:
        data = bank_recon_report.render_pdf(snapshot, on, by)
        media, name = "application/pdf", f"bank-reconciliation-{stamp}.pdf"
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/reconciliations/{recon_id}/finalize")
async def finalize(recon_id: uuid.UUID, user: CurrentUser, token: BearerToken,
                   db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    rec = await _recon(db, recon_id)
    acct = await _account(db, rec.bank_account_id)
    snapshot = await _snapshot(db, rec, acct, user.get("name") or "")
    try:
        await crud.finalize(db, rec, acct, _actor(user), snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    pdf = bank_recon_report.render_pdf(snapshot, rec.finalized_at.date(),
                                       snapshot.get("prepared_by") or "")
    key, warning = await bank_files.store(
        pdf, f"bank-reconciliation-{rec.period_end.isoformat()}.pdf", "application/pdf",
        bank_files.DOC_RECON, rec.id, token)
    if key:
        rec.report_storage_key = key
    await db.commit()
    return {"status": rec.status, "finalized_at": rec.finalized_at.isoformat(),
            "retention_warning": warning, "summary": snapshot["summary"]}


@router.post("/reconciliations/{recon_id}/reopen")
async def reopen(recon_id: uuid.UUID, user: CurrentUser,
                 db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    rec = await _recon(db, recon_id)
    if rec.status != FINALIZED:
        raise HTTPException(status_code=409, detail="That period is not finalized.")
    await crud.reopen(db, rec, _actor(user))
    await db.commit()
    return {"status": rec.status}


@router.post("/heal")
async def heal(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Re-point ledger references orphaned by a full NC sync. Safe to run any
    time; finalized periods are skipped."""
    await _require_manage(db, user)
    result = await crud.heal_matches(db)
    await db.commit()
    return result
