"""Accounts Receivable API (Phase c framework) — finance-owned AR invoices,
revenue recognition, customer receipts, open items / aging."""
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.coa import _require_manage
from app.core.deps import CurrentUser
from app.crud import ar as ar_crud
from app.db.base import get_db
from app.models.ar import DRAFT, VOID, ArInvoice, ArInvoiceTaxLine

router = APIRouter(prefix="/ar", tags=["accounts-receivable"])


# ── schemas ───────────────────────────────────────────────────────────────────────

class TaxLineIn(BaseModel):
    tax_code: str
    taxable_base: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")


class InvoiceIn(BaseModel):
    customer_id: uuid.UUID
    customer_name: str = Field(min_length=1, max_length=255)
    invoice_date: date
    due_date: date
    currency: str = "CAD"
    amount: Decimal                       # pre-tax
    tax_lines: list[TaxLineIn] = []
    invoice_number: str | None = None
    description: str | None = None


class TaxLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    line_no: int
    tax_code: str
    taxable_base: str
    tax_amount: str

    @field_validator("taxable_base", "tax_amount", mode="before")
    @classmethod
    def _s(cls, v): return str(v)


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    invoice_number: str
    customer_id: uuid.UUID
    customer_name: str
    amount: str
    tax_amount: str
    total_amount: str
    paid_amount: str
    currency: str
    invoice_date: date
    due_date: date
    status: str
    description: str | None

    @field_validator("amount", "tax_amount", "total_amount", "paid_amount", mode="before")
    @classmethod
    def _s(cls, v): return str(v)


class ReceiptIn(BaseModel):
    customer_id: uuid.UUID
    customer_name: str = Field(min_length=1, max_length=255)
    amount: Decimal
    currency: str = "CAD"
    receipt_date: date
    invoice_id: uuid.UUID | None = None
    method: str = "bank_transfer"
    bank_account_id: uuid.UUID | None = None
    reference: str | None = None


# ── invoices ────────────────────────────────────────────────────────────────────

@router.post("/invoices", response_model=InvoiceOut, status_code=201)
async def create_invoice(body: InvoiceIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    inv = await ar_crud.create_invoice(
        db, customer_id=body.customer_id, customer_name=body.customer_name,
        invoice_date=body.invoice_date, due_date=body.due_date, currency=body.currency,
        amount=body.amount, lines_tax=[t.model_dump() for t in body.tax_lines],
        invoice_number=body.invoice_number, description=body.description,
    )
    await db.commit()
    return inv


@router.get("/invoices", response_model=list[InvoiceOut])
async def list_invoices(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        status: str | None = Query(default=None),
                        customer_id: uuid.UUID | None = Query(default=None),
                        limit: int = Query(default=200, le=1000)):
    q = select(ArInvoice)
    if status:
        q = q.where(ArInvoice.status == status)
    if customer_id:
        q = q.where(ArInvoice.customer_id == customer_id)
    return list((await db.execute(q.order_by(ArInvoice.invoice_date.desc()).limit(limit))).scalars().all())


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: uuid.UUID, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    inv = (await db.execute(select(ArInvoice).where(ArInvoice.id == invoice_id))).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="AR invoice not found")
    tax = (await db.execute(
        select(ArInvoiceTaxLine).where(ArInvoiceTaxLine.invoice_id == invoice_id)
        .order_by(ArInvoiceTaxLine.line_no))).scalars().all()
    return {"invoice": InvoiceOut.model_validate(inv).model_dump(),
            "tax_lines": [TaxLineOut.model_validate(t).model_dump() for t in tax]}


@router.post("/invoices/{invoice_id}/post")
async def post_invoice(invoice_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Revenue recognition: emit debit AR / credit revenue / credit output_tax (idempotent)."""
    await _require_manage(db, user)
    try:
        result = await ar_crud.post_invoice_revenue(db, invoice_id)
        await db.commit()
        return result
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceOut)
async def void_invoice(invoice_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Void a draft (un-posted) invoice. Voiding a posted invoice needs a
    reversing entry — deferred to GL (raise 409 for now)."""
    await _require_manage(db, user)
    inv = (await db.execute(select(ArInvoice).where(ArInvoice.id == invoice_id))).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="AR invoice not found")
    if inv.status != DRAFT:
        raise HTTPException(status_code=409,
                            detail="Only draft invoices can be voided (posted reversal lands at GL)")
    inv.status = VOID
    await db.commit()
    return inv


# ── receipts ──────────────────────────────────────────────────────────────────────

@router.post("/receipts", status_code=201)
async def record_receipt(body: ReceiptIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Cash received: debit bank / credit AR; applies to an invoice (or on-account)."""
    await _require_manage(db, user)
    try:
        result = await ar_crud.record_receipt(
            db, customer_id=body.customer_id, customer_name=body.customer_name,
            amount=body.amount, currency=body.currency, receipt_date=body.receipt_date,
            invoice_id=body.invoice_id, method=body.method,
            bank_account_id=body.bank_account_id, reference=body.reference,
            recorded_by=uuid.UUID(user["sub"]),
        )
        await db.commit()
        return result
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── open items / aging ────────────────────────────────────────────────────────────

class OpenItem(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: str
    customer_id: uuid.UUID
    customer_name: str
    total_amount: str
    paid_amount: str
    outstanding: str
    currency: str
    invoice_date: str
    due_date: str
    days_overdue: int
    status: str


@router.get("/open-items", response_model=list[OpenItem])
async def open_items(_: CurrentUser, db: AsyncSession = Depends(get_db),
                     customer_id: uuid.UUID | None = Query(default=None),
                     limit: int = Query(default=200, le=1000)):
    rows = await ar_crud.open_items(db, customer_id=customer_id, limit=limit)
    today = date.today()
    return [
        OpenItem(
            invoice_id=r.id, invoice_number=r.invoice_number, customer_id=r.customer_id,
            customer_name=r.customer_name, total_amount=str(r.total_amount),
            paid_amount=str(r.paid_amount), outstanding=str(r.total_amount - r.paid_amount),
            currency=r.currency, invoice_date=r.invoice_date.isoformat(),
            due_date=r.due_date.isoformat(), days_overdue=(today - r.due_date).days, status=r.status,
        ) for r in rows
    ]


@router.get("/aging")
async def aging(_: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await ar_crud.aging(db)
