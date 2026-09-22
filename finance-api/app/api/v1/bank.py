"""Bank accounts, statement import, reconciliation, FX rates API (Phase a A3)."""
import json
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.coa import _require_manage  # shared can_manage gate (roles ∪ assignments)
from app.core.deps import CurrentUser
from app.crud import bank as bank_crud
from app.db.base import get_db
from app.models.bank import MATCHED, UNMATCHED, BankAccount, BankTransaction, ExchangeRate
from app.models.payment import PaymentRecord

router = APIRouter(prefix="/bank", tags=["bank-reconciliation"])


# ── schemas ─────────────────────────────────────────────────────────────────────

class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    bank_name: str = Field(min_length=1, max_length=255)
    kind: str = "bank"                       # bank | credit_card
    account_masked: str | None = None
    currency: str = "CAD"
    ledger_account_code: str | None = None
    # Which NC bank account this IS (BD_BANKACCSUB.CODE, e.g. '1033760'). The
    # reconciliation reads its ledger side through this; unset means the account
    # has no book side at all. See services/bank_book.py.
    nc_bank_account_code: str | None = None
    is_active: bool = True


class AccountOut(AccountIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    import_mapping: dict | None = None


class TxnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    txn_date: date
    description: str
    reference: str | None
    amount: Decimal
    currency: str
    status: str
    matched_payment_id: uuid.UUID | None


class RateIn(BaseModel):
    from_currency: str = Field(min_length=3, max_length=10)
    to_currency: str = "CAD"
    rate: Decimal
    effective_date: date


# ── accounts ────────────────────────────────────────────────────────────────────

@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(_: CurrentUser, db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(BankAccount).order_by(BankAccount.name))).scalars().all()


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def create_account(body: AccountIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    acct = BankAccount(**body.model_dump())
    db.add(acct)
    await db.flush()
    await db.commit()
    return acct


@router.put("/accounts/{account_id}", response_model=AccountOut)
async def update_account(account_id: uuid.UUID, body: AccountIn, user: CurrentUser,
                         db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    acct = await _get_account(db, account_id)
    for k, v in body.model_dump().items():
        setattr(acct, k, v)
    await db.flush()
    await db.commit()
    return acct


async def _get_account(db: AsyncSession, account_id: uuid.UUID) -> BankAccount:
    acct = (await db.execute(
        select(BankAccount).where(BankAccount.id == account_id)
    )).scalar_one_or_none()
    if acct is None:
        raise HTTPException(status_code=404, detail="Bank account not found")
    return acct


# ── statement import ────────────────────────────────────────────────────────────

@router.post("/{account_id}/import")
async def import_statement(
    account_id: uuid.UUID, user: CurrentUser,
    file: UploadFile,
    mapping: str | None = Form(default=None),
    save_mapping: bool = Form(default=True),
    db: AsyncSession = Depends(get_db),
):
    """Import a statement CSV. `mapping` is a JSON column map (date/description/
    reference + signed `amount` OR `debit`+`credit`, with date_format/default_year).
    Omit it to reuse the account's saved preset. save_mapping persists it for reuse."""
    await _require_manage(db, user)
    acct = await _get_account(db, account_id)
    parsed_map: dict | None = None
    if mapping:
        try:
            parsed_map = json.loads(mapping)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="mapping must be valid JSON")
    content = await file.read()
    result = await bank_crud.import_statement_csv(
        db, acct, content.decode("utf-8-sig"), mapping=parsed_map)
    if parsed_map and save_mapping and result.get("imported", 0) >= 0 and not result.get("errors"):
        acct.import_mapping = parsed_map
    await db.commit()
    return result


@router.get("/{account_id}/transactions", response_model=list[TxnOut])
async def list_transactions(
    account_id: uuid.UUID, _: CurrentUser, db: AsyncSession = Depends(get_db),
    status: str | None = Query(default=None),
):
    q = select(BankTransaction).where(BankTransaction.bank_account_id == account_id)
    if status:
        q = q.where(BankTransaction.status == status)
    return (await db.execute(q.order_by(BankTransaction.txn_date.desc()))).scalars().all()


# ── matching ────────────────────────────────────────────────────────────────────

@router.post("/match/auto")
async def auto_match(
    account_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db),
    window_days: int = Query(default=5, ge=0, le=60),
):
    await _require_manage(db, user)
    acct = await _get_account(db, account_id)
    result = await bank_crud.auto_match(db, acct, window_days=window_days)
    await db.commit()
    return result


class ManualMatchRequest(BaseModel):
    payment_record_id: uuid.UUID


@router.post("/transactions/{txn_id}/match", response_model=TxnOut)
async def match_txn(
    txn_id: uuid.UUID, body: ManualMatchRequest, user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    await _require_manage(db, user)
    txn = (await db.execute(
        select(BankTransaction).where(BankTransaction.id == txn_id)
    )).scalar_one_or_none()
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    try:
        txn = await bank_crud.manual_match(db, txn, body.payment_record_id, uuid.UUID(user["sub"]))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await db.commit()
    return txn


@router.post("/transactions/{txn_id}/unmatch", response_model=TxnOut)
async def unmatch_txn(txn_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    txn = (await db.execute(
        select(BankTransaction).where(BankTransaction.id == txn_id)
    )).scalar_one_or_none()
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    txn = await bank_crud.unmatch(db, txn)
    await db.commit()
    return txn


class ExcludeRequest(BaseModel):
    excluded: bool = True


@router.post("/transactions/{txn_id}/exclude", response_model=TxnOut)
async def exclude_txn(txn_id: uuid.UUID, body: ExcludeRequest, user: CurrentUser,
                      db: AsyncSession = Depends(get_db)):
    """Exclude (or re-include) a line — bank fees, interest, internal FX transfers
    that have no payment_record counterpart."""
    await _require_manage(db, user)
    txn = (await db.execute(
        select(BankTransaction).where(BankTransaction.id == txn_id)
    )).scalar_one_or_none()
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    txn = await bank_crud.set_excluded(db, txn, body.excluded)
    await db.commit()
    return txn


# ── reconciliation summary (FIN-CASH-002) ───────────────────────────────────────

@router.get("/reconciliation")
async def reconciliation(
    account_id: uuid.UUID, _: CurrentUser, db: AsyncSession = Depends(get_db),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
):
    acct = await _get_account(db, account_id)
    q = select(BankTransaction).where(BankTransaction.bank_account_id == account_id)
    if date_from:
        q = q.where(BankTransaction.txn_date >= date_from)
    if date_to:
        q = q.where(BankTransaction.txn_date <= date_to)
    txns = (await db.execute(q)).scalars().all()

    inflow = sum((t.amount for t in txns if t.amount > 0), Decimal("0"))
    outflow = sum((t.amount for t in txns if t.amount < 0), Decimal("0"))
    matched_total = sum((t.amount for t in txns if t.status == MATCHED), Decimal("0"))
    unmatched = [t for t in txns if t.status == UNMATCHED]

    # payments in the period not matched to any statement line (the other side
    # of the discrepancy list — paid in the ledger but not seen on the bank)
    claimed = await bank_crud._claimed_payment_ids(db)
    pq = select(PaymentRecord)
    if date_from:
        pq = pq.where(PaymentRecord.payment_date >= date_from)
    if date_to:
        pq = pq.where(PaymentRecord.payment_date <= date_to)
    pays = (await db.execute(pq)).scalars().all()
    unreconciled_payments = [
        {"payment_record_id": str(p.id), "doc_number": p.doc_number or p.pa_number,
         "amount": str(p.amount), "currency": p.currency,
         "payment_date": p.payment_date.isoformat()}
        for p in pays if p.id not in claimed
    ]

    return {
        "account": {"id": str(acct.id), "name": acct.name, "currency": acct.currency},
        "inflow": str(inflow),
        "outflow": str(outflow),
        "matched_total": str(matched_total),
        "unmatched_count": len(unmatched),
        "unmatched_transactions": [
            {"id": str(t.id), "txn_date": t.txn_date.isoformat(),
             "description": t.description, "amount": str(t.amount)}
            for t in unmatched
        ],
        "unreconciled_payments": unreconciled_payments,
    }


# ── FX rates (FIN-CASH-003) ──────────────────────────────────────────────────────

@router.get("/rates", response_model=list[dict])
async def list_rates(_: CurrentUser, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(ExchangeRate).order_by(ExchangeRate.effective_date.desc()).limit(200)
    )).scalars().all()
    return [
        {"from_currency": r.from_currency, "to_currency": r.to_currency,
         "rate": str(r.rate), "effective_date": r.effective_date.isoformat()}
        for r in rows
    ]


@router.post("/rates", status_code=201)
async def upsert_rate(body: RateIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    existing = (await db.execute(
        select(ExchangeRate).where(
            ExchangeRate.from_currency == body.from_currency,
            ExchangeRate.to_currency == body.to_currency,
            ExchangeRate.effective_date == body.effective_date,
        )
    )).scalar_one_or_none()
    if existing:
        existing.rate = body.rate
    else:
        db.add(ExchangeRate(**body.model_dump()))
    await db.flush()
    await db.commit()
    return {"ok": True}
