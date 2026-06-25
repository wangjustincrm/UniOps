import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.crud import ap as ap_crud
from app.crud import ap_invoice as ap_invoice_crud
from app.crud.ap_accrual import post_invoice_accrual
from app.schemas.ap import APPayableListResponse, APPayableResponse

router = APIRouter(prefix="/ap", tags=["accounts-payable"])


class PostInvoiceRequest(BaseModel):
    invoice_id: uuid.UUID


class MarkSettledRequest(BaseModel):
    invoice_ids: list[uuid.UUID]


class OpenItem(BaseModel):
    invoice_id: uuid.UUID
    internal_ref: str
    vendor_invoice_number: str
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    total_amount: str
    currency: str
    invoice_date: str
    due_date: str
    days_overdue: int          # negative = not yet due
    status: str


@router.get("/open-items", response_model=list[OpenItem])
async def open_items(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    vendor_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
):
    """A2: unpaid open AP invoices, ordered by due date (FIN-AP-006).
    Reads finance-owned ap_invoices (posted / partially_paid)."""
    from datetime import date as _date

    rows = await ap_invoice_crud.open_items(db, vendor_id=vendor_id, limit=limit)
    today = _date.today()
    return [
        OpenItem(
            invoice_id=r.id, internal_ref=r.ap_invoice_number,
            vendor_invoice_number=r.vendor_invoice_number or "",
            vendor_id=r.vendor_id, vendor_name=r.vendor_name,
            total_amount=str(r.total_amount), currency=r.currency,
            invoice_date=r.invoice_date.isoformat(),
            due_date=r.due_date.isoformat() if r.due_date else "",
            days_overdue=((today - r.due_date).days if r.due_date else 0), status=r.status,
        )
        for r in rows
    ]


@router.get("/aging")
async def aging(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    """AP aging by vendor and currency: current / 1-30 / 31-60 / 61-90 / 90+.
    Reads finance-owned ap_invoices (mirror of AR's /ar/aging)."""
    return await ap_invoice_crud.aging(db)


@router.post("/post-invoice")
async def post_invoice(body: PostInvoiceRequest, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    """Bridge: EPMS calls this on match. Read the EPMS mirror invoice, upsert it
    into finance-owned ap_invoices (posted), then emit the AP accrual.
    (Plan 2 will replace this with EPMS calling /ap/invoices directly.)"""
    from sqlalchemy import select
    from app.models.mirrors import Invoice, InvoiceTaxLine
    from app.crud import ap_invoice as ap_invoice_crud
    from app.crud.ap_accrual import post_invoice_accrual

    inv = (await db.execute(select(Invoice).where(Invoice.id == body.invoice_id))).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    tax = (await db.execute(
        select(InvoiceTaxLine).where(InvoiceTaxLine.invoice_id == inv.id)
        .order_by(InvoiceTaxLine.line_no))).scalars().all()
    ap = await ap_invoice_crud.upsert(
        db, source="epms", source_invoice_id=inv.id,
        payload={"source_ref": inv.internal_ref, "vendor_id": inv.vendor_id,
                 "vendor_name": inv.vendor_name, "vendor_invoice_number": inv.vendor_invoice_number,
                 "amount": inv.amount, "tax_amount": inv.tax_amount, "total_amount": inv.total_amount,
                 "currency": inv.currency, "invoice_date": inv.invoice_date, "due_date": inv.due_date,
                 "status": "posted", "source_status": inv.status},
        tax_lines=[{"line_no": t.line_no, "tax_code": t.tax_code, "tax_amount": t.tax_amount,
                    "recoverable": t.recoverable} for t in tax],
    )
    try:
        result = await post_invoice_accrual(db, ap.id)
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/mark-settled")
async def mark_settled(body: MarkSettledRequest, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    """Zero-cash reconciliation: a prepayment already covered these invoices, so
    flip their finance-owned ap_invoices to paid WITHOUT a payment record or bank
    posting (the prepayment's payment already moved the cash). Idempotent — only
    touches posted/partially_paid rows."""
    from sqlalchemy import select
    from app.models.ap_invoice import ApInvoice

    marked = 0
    for iid in body.invoice_ids:
        ap = (await db.execute(
            select(ApInvoice).where(ApInvoice.source_invoice_id == iid)
        )).scalar_one_or_none()
        if ap and ap.status in ("posted", "partially_paid"):
            ap.status = "paid"
            ap.paid_amount = ap.total_amount
            marked += 1
    await db.commit()
    return {"marked": marked}


@router.get("/payables", response_model=APPayableListResponse)
async def list_payables(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    status: str | None = Query(default="approved"),
    vendor_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    items, total = await ap_crud.get_payables(db, status=status, vendor_id=vendor_id, page=page, page_size=page_size)
    return APPayableListResponse(
        items=[
            APPayableResponse(
                pa_id=p.id,
                pa_number=p.pa_number,
                pa_type=p.pa_type,
                status=p.status,
                vendor_id=p.vendor_id,
                vendor_name=p.vendor_name,
                po_number=p.po_number,
                payment_amount=p.payment_amount,
                currency=p.currency,
                invoice_count=len(p.invoice_ids),
                submitted_at=p.submitted_at,
                expected_settlement_date=p.expected_settlement_date,
            )
            for p in items
        ],
        total=total,
    )


@router.get("/payables/{pa_id}", response_model=APPayableResponse)
async def get_payable(pa_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    p = await ap_crud.get_payable_by_id(db, pa_id)
    if not p:
        raise HTTPException(status_code=404, detail="Payment application not found")
    return APPayableResponse(
        pa_id=p.id, pa_number=p.pa_number, pa_type=p.pa_type, status=p.status,
        vendor_id=p.vendor_id, vendor_name=p.vendor_name, po_number=p.po_number,
        payment_amount=p.payment_amount, currency=p.currency, invoice_count=len(p.invoice_ids),
        submitted_at=p.submitted_at, expected_settlement_date=p.expected_settlement_date,
    )
