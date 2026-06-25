import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import BearerToken, CurrentUser
from app.db.base import get_db
from app.crud import payment as payment_crud
from app.crud import payment_batch as batch_crud
from app.crud import payment_execute
from app.crud.payment_execute import PaymentPermissionError
from app.models.payment_batch import PaymentBatch, PaymentBatchLine
from app.schemas.payment import PaymentListResponse, PaymentResponse
from app.schemas.payment_execute import PaymentExecuteRequest, PaymentExecuteResponse

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/execute", response_model=PaymentExecuteResponse)
async def execute_payment(
    body: PaymentExecuteRequest,
    token: BearerToken,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = ...,
):
    """Unified payment executor (Phase 0-B1.5) — THE single implementation of
    'money goes out' for PA / Direct PA / Expense Claim. Legacy entries in
    epms-api and expense-api forward here."""
    try:
        result = await payment_execute.execute(db, body, user, bearer_token=token)
        await db.commit()
        return result
    except PaymentPermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("", status_code=410)
async def record_payment_deprecated(_: CurrentUser = ...):
    """DEPRECATED (Phase a A0): manual payment_records entry was never wired to
    any frontend; /payments/execute writes the record as part of the unified
    payment flow. Returns 410 Gone."""
    raise HTTPException(
        status_code=410,
        detail="Deprecated — use POST /finance/v1/payments/execute",
    )


@router.get("", response_model=PaymentListResponse)
async def list_payments(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    pa_id: uuid.UUID | None = Query(default=None),
    vendor_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    items, total = await payment_crud.get_all(db, pa_id=pa_id, vendor_id=vendor_id, page=page, page_size=page_size)
    return PaymentListResponse(items=items, total=total)


# NOTE: GET /{payment_id} is defined at the END of this file so literal routes
# like /due and /batches are matched first (route order matters in Starlette).


# ── Payment batches / runs (A4) ──────────────────────────────────────────────────

class BatchLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    doc_kind: str
    doc_id: uuid.UUID
    doc_number: str | None
    amount: str
    status: str
    error: str | None

    @field_validator("amount", mode="before")
    @classmethod
    def _amt(cls, v): return str(v)


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    batch_number: str
    batch_date: date
    status: str
    currency: str
    total: str
    payment_method: str
    bank_account_id: uuid.UUID | None = None

    @field_validator("total", mode="before")
    @classmethod
    def _tot(cls, v): return str(v)


class DocRef(BaseModel):
    doc_kind: str
    doc_id: uuid.UUID


class CreateBatchRequest(BaseModel):
    docs: list[DocRef]
    payment_method: str = "bank_transfer"
    batch_date: date | None = None


class ExecuteBatchRequest(BaseModel):
    bank_account_id: uuid.UUID | None = None


@router.get("/can-pay")
async def can_pay(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Whether the current user may execute payments (ap_clerk / finance roles or
    a finance_bp / finance_manager role_management assignment). Used by the
    Payment Batches UI to show the Create/Execute controls — same gate the
    create_batch / execute_batch endpoints enforce server-side."""
    try:
        await payment_execute._check_can_pay(db, user)
        return {"can_pay": True}
    except PaymentPermissionError:
        return {"can_pay": False}


@router.get("/due")
async def list_due(_: CurrentUser, db: AsyncSession = Depends(get_db),
                   currency: str | None = Query(default=None)):
    """Approved PAs awaiting payment — pickable rows for a payment run."""
    return await batch_crud.list_due(db, currency=currency)


@router.get("/batches", response_model=list[BatchOut])
async def list_batches(_: CurrentUser, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(PaymentBatch).order_by(PaymentBatch.created_at.desc()).limit(200)
    )).scalars().all()
    return rows


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: uuid.UUID, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    batch = (await db.execute(
        select(PaymentBatch).where(PaymentBatch.id == batch_id)
    )).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    lines = (await db.execute(
        select(PaymentBatchLine).where(PaymentBatchLine.batch_id == batch_id)
        .order_by(PaymentBatchLine.created_at)
    )).scalars().all()
    return {
        "batch": BatchOut.model_validate(batch).model_dump(),
        "lines": [BatchLineOut.model_validate(ln).model_dump() for ln in lines],
    }


@router.post("/batches", response_model=BatchOut, status_code=201)
async def create_batch(body: CreateBatchRequest, user: CurrentUser,
                       db: AsyncSession = Depends(get_db)):
    # payment authority (can_pay incl. ap_clerk + assignments), not COA-manage
    try:
        await payment_execute._check_can_pay(db, user)
    except PaymentPermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    try:
        batch = await batch_crud.create_batch(
            db, docs=[(d.doc_kind, d.doc_id) for d in body.docs],
            payment_method=body.payment_method,
            batch_date=body.batch_date or date.today(),
            created_by=uuid.UUID(user["sub"]),
        )
        await db.commit()
        return batch
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/batches/{batch_id}/execute")
async def execute_batch(batch_id: uuid.UUID, token: BearerToken, user: CurrentUser,
                        body: ExecuteBatchRequest = ExecuteBatchRequest(),
                        db: AsyncSession = Depends(get_db)):
    try:
        await payment_execute._check_can_pay(db, user)
    except PaymentPermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    batch = (await db.execute(
        select(PaymentBatch).where(PaymentBatch.id == batch_id)
    )).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    try:
        await batch_crud.execute_batch(db, batch, user, bearer_token=token,
                                       bank_account_id=body.bank_account_id)
        await db.commit()
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    lines = (await db.execute(
        select(PaymentBatchLine).where(PaymentBatchLine.batch_id == batch_id))).scalars().all()
    return {
        "batch": BatchOut.model_validate(batch).model_dump(),
        "paid": sum(1 for l in lines if l.status == "paid"),
        "failed": sum(1 for l in lines if l.status == "failed"),
        "lines": [BatchLineOut.model_validate(ln).model_dump() for ln in lines],
    }


# defined last so /due, /batches, /batches/{id} are matched before the catch-all
@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(payment_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    p = await payment_crud.get_by_id(db, payment_id)
    if not p:
        raise HTTPException(status_code=404, detail="Payment record not found")
    return p
