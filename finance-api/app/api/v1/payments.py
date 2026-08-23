import csv
import io
import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import BearerToken, CurrentUser
from app.core.read_authz import authorize_finance_read
from app.db.base import get_db
from app.crud import payment as payment_crud
from app.crud import payment_batch as batch_crud
from app.crud import payment_execute
from app.crud.payment_execute import PaymentPermissionError
from app.models.mirrors import ExpenseClaim
from app.models.payment_batch import PaymentBatch, PaymentBatchLine
from app.models.remittance import SENT
from app.schemas.payment import PaymentListResponse, PaymentResponse, PaymentSummaryRow
from app.schemas.payment_execute import PaymentExecuteRequest, PaymentExecuteResponse

router = APIRouter(prefix="/payments", tags=["payments"])

# Mounted immediately, before any route in this module is declared (including
# the catch-all GET /{payment_id} at the very bottom) — so
# /{payment_id}/remittance/preview and /send are registered ahead of it. See
# app/api/v1/remittance.py's module docstring for why route order matters
# here.
from app.api.v1.remittance import router as remittance_router  # noqa: E402

router.include_router(remittance_router)


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


async def _authorize_read(db: AsyncSession, user: dict) -> None:
    """Thin alias kept for this module's call sites.

    The rule itself now lives in app/core/read_authz.py so
    app/api/v1/vendor_credits.py's /suggest can share it verbatim rather than
    grow a second copy that drifts. See that module for the rationale.
    """
    await authorize_finance_read(db, user)


class PaymentFilters(BaseModel):
    pa_id: uuid.UUID | None = None
    vendor_id: uuid.UUID | None = None
    date_from: date | None = None
    date_to: date | None = None
    doc_kind: str | None = None
    currency: str | None = None
    payment_method: str | None = None
    status: str | None = None
    bank_account_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    source: str | None = None          # batch | single
    remittance: str | None = None      # sent | not_sent — page-independent filter only;
    # richer blocked states (see _remittance_status) are computed live per page.
    q: str | None = None


async def _remittance_status(db: AsyncSession, records: list) -> dict[uuid.UUID, str]:
    """'sent' / 'not_sent' for the CURRENT PAGE only — block reasons are live
    and too costly to evaluate across an unbounded result set."""
    if not records:
        return {}
    ids = [str(r.id) for r in records]
    sent = set((await db.execute(sa.text(
        "SELECT DISTINCT jsonb_array_elements_text(payment_record_ids) AS rid"
        " FROM payment_remittance_notifications"
        " WHERE status = :s AND payment_record_ids ?| :ids"
    ), {"s": SENT, "ids": ids})).scalars().all())
    return {r.id: ("sent" if str(r.id) in sent else "not_sent") for r in records}


async def _payee_names(db: AsyncSession, records: list) -> dict[uuid.UUID, str]:
    """Vendor name for vendor payments (already on the record); the claimant's
    name for claim payments, which carry no vendor columns at all."""
    out = {r.id: (r.vendor_name or "") for r in records}
    claim_ids = [r.doc_id for r in records
                 if r.doc_kind == "expense_claim" and r.doc_id]
    if claim_ids:
        rows = (await db.execute(
            select(ExpenseClaim.id, ExpenseClaim.employee_name)
            .where(ExpenseClaim.id.in_(claim_ids))
        )).all()
        name_by_claim = dict(rows)
        for r in records:
            if r.doc_kind == "expense_claim":
                out[r.id] = name_by_claim.get(r.doc_id, "")
    return out


@router.get("", response_model=PaymentListResponse)
async def list_payments(
    filters: PaymentFilters = Depends(),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = ...,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    await _authorize_read(db, user)
    items, total = await payment_crud.get_all(
        db, page=page, page_size=page_size, **filters.model_dump())
    status_by_id = await _remittance_status(db, items)
    payee_by_id = await _payee_names(db, items)
    out = []
    for r in items:
        d = PaymentResponse.model_validate(r).model_dump()
        d["payee_name"] = payee_by_id.get(r.id) or None
        d["remittance_status"] = status_by_id.get(r.id)
        out.append(d)
    return PaymentListResponse(items=out, total=total)


@router.get("/summary", response_model=list[PaymentSummaryRow])
async def payments_summary(filters: PaymentFilters = Depends(),
                           db: AsyncSession = Depends(get_db),
                           user: CurrentUser = ...):
    """Totals over the WHOLE filtered set, not the current page — a separate
    endpoint rather than something derived from the page for exactly that
    reason."""
    await _authorize_read(db, user)
    return await payment_crud.summary(db, **filters.model_dump())


@router.get("/export")
async def export_payments(filters: PaymentFilters = Depends(),
                          db: AsyncSession = Depends(get_db),
                          user: CurrentUser = ...):
    """CSV of the whole filtered set — pagination deliberately ignored."""
    await _authorize_read(db, user)
    rows = await payment_crud.export_rows(db, **filters.model_dump())
    payee_by_id = await _payee_names(db, rows)

    def _iter():
        buf = io.StringIO()
        w = csv.writer(buf)
        # `amount` stays the NET cash that left the bank (column position and
        # meaning unchanged for anyone with an existing import). `gross` and
        # `credit_applied` are appended so a short payment is explicable from
        # the export alone: gross = amount + credit_applied.
        w.writerow(["payment_date", "doc_kind", "doc_number", "payee", "amount",
                    "currency", "payment_method", "source", "status",
                    "gross", "credit_applied"])
        yield buf.getvalue()
        for r in rows:
            buf.seek(0), buf.truncate(0)
            credit = r.credit_applied or Decimal("0.00")
            w.writerow([r.payment_date, r.doc_kind or "", r.doc_number or "",
                        payee_by_id.get(r.id) or "", r.amount, r.currency,
                        r.payment_method, "batch" if r.batch_id else "single",
                        r.status, r.amount + credit, credit])
            yield buf.getvalue()

    return StreamingResponse(_iter(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="payments.csv"'})


# NOTE: GET /{payment_id} is defined at the END of this file so literal routes
# like /due and /batches are matched first (route order matters in Starlette).


# ── Payment batches / runs (A4) ──────────────────────────────────────────────────

class BatchLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    doc_kind: str
    doc_id: uuid.UUID
    doc_number: str | None
    vendor_inv_no: str = ""
    amount: str
    status: str
    error: str | None

    @field_validator("amount", mode="before")
    @classmethod
    def _amt(cls, v): return str(v)


async def _lines_out(db: AsyncSession, lines: list[PaymentBatchLine]) -> list[dict]:
    """Serialize batch lines with each PA line's vendor invoice number(s)
    resolved at read time (see crud.vendor_inv_no_for_lines)."""
    inv_no = await batch_crud.vendor_inv_no_for_lines(db, lines)
    out = []
    for ln in lines:
        d = BatchLineOut.model_validate(ln).model_dump()
        d["vendor_inv_no"] = inv_no.get(ln.doc_id, "")
        out.append(d)
    return out


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
    # doc_id -> credit ids to apply. A document absent from the map uses the
    # automatic FIFO default; an explicit empty list pays that line in full.
    credit_ids_by_doc: dict[uuid.UUID, list[uuid.UUID]] | None = None


@router.get("/can-pay")
async def can_pay(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Whether the current user may execute payments — primary (JWT) role in
    _PAY_ROLES, or an ADDITIONAL role (identity's user_roles) in
    _PAY_ROLES_ASSIGNED (_PAY_ROLES minus system_admin): payment_officer /
    finance_manager / finance_bp, or system_admin as a primary role only.
    ap_clerk is deliberately NOT in this set (2026-08-13: payment execution
    moved to payment_officer; ap_clerk keeps read access, see _FINANCE_ROLES
    in app/core/deps.py). Used by the Payment Batches UI to show the
    Create/Execute controls — same gate the create_batch / execute_batch
    endpoints enforce server-side."""
    try:
        await payment_execute._check_can_pay(db, user)
        return {"can_pay": True}
    except PaymentPermissionError:
        return {"can_pay": False}


@router.get("/due")
async def list_due(user: CurrentUser, db: AsyncSession = Depends(get_db),
                   currency: str | None = Query(default=None)):
    """Approved PAs awaiting payment — pickable rows for a payment run.

    Every approved PA and expense claim awaiting payment, employee names and
    amounts included — the same read authority as list/summary/export below
    (see `_authorize_read`), not just a valid token. An OA-only employee with
    no finance role could otherwise enumerate this the same way `/export`
    was fixed to prevent."""
    await _authorize_read(db, user)
    return await batch_crud.list_due(db, currency=currency)


@router.get("/batches", response_model=list[BatchOut])
async def list_batches(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await _authorize_read(db, user)
    rows = (await db.execute(
        select(PaymentBatch).order_by(PaymentBatch.created_at.desc()).limit(200)
    )).scalars().all()
    return rows


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Full line breakdown for one batch, vendor invoice numbers included —
    same read authority as the list above, for the same reason."""
    await _authorize_read(db, user)
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
        "lines": await _lines_out(db, lines),
    }


@router.post("/batches", response_model=BatchOut, status_code=201)
async def create_batch(body: CreateBatchRequest, user: CurrentUser,
                       db: AsyncSession = Depends(get_db)):
    # payment authority: primary role in _PAY_ROLES or an additional role in
    # _PAY_ROLES_ASSIGNED (payment_officer / finance_manager / finance_bp —
    # ap_clerk removed 2026-08-13), not COA-manage
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
                                       bank_account_id=body.bank_account_id,
                                       credit_ids_by_doc=body.credit_ids_by_doc)
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
        "lines": await _lines_out(db, lines),
    }


# defined last so /due, /batches, /batches/{id} are matched before the catch-all
@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(payment_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    p = await payment_crud.get_by_id(db, payment_id)
    if not p:
        raise HTTPException(status_code=404, detail="Payment record not found")
    return p
