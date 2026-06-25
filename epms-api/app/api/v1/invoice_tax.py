"""Invoice tax lines — line-level GST/HST/PST/QST split (Phase 0-B2).

PUT replaces the full set and derives the header: invoice.tax_amount = Σ lines,
total_amount = amount + tax_amount.

Permissions follow the EPMS Access Control Matrix (same gate as invoice
editing: `invoice_upload`), NOT hardcoded roles — matrix toggles are the
EPMS convention and what the UI's editable flag is based on.
"""
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from app.core.deps import CurrentUserPayload, SessionDep, require_permission
from app.models.invoice import Invoice
from app.models.invoice_tax_line import InvoiceTaxLine

router = APIRouter(prefix="/invoices", tags=["invoice-tax"])

InvoiceEditDep = Annotated[dict, Depends(require_permission("invoice_upload"))]


class TaxLineIn(BaseModel):
    tax_code: str = Field(min_length=1, max_length=20)
    tax_amount: Decimal
    taxable_amount: Decimal | None = None
    recoverable: bool = True


class TaxLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    line_no: int
    tax_code: str
    taxable_amount: Decimal | None
    tax_amount: Decimal
    recoverable: bool


class TaxLinesResponse(BaseModel):
    invoice_id: uuid.UUID
    tax_amount: Decimal       # derived header value
    total_amount: Decimal
    lines: list[TaxLineOut]


async def _get_invoice(db, invoice_id: uuid.UUID) -> Invoice:
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return inv


async def _lines_of(db, invoice_id: uuid.UUID) -> list[InvoiceTaxLine]:
    return list((await db.execute(
        select(InvoiceTaxLine)
        .where(InvoiceTaxLine.invoice_id == invoice_id)
        .order_by(InvoiceTaxLine.line_no)
    )).scalars().all())


@router.get("/{invoice_id}/tax-lines", response_model=TaxLinesResponse)
async def get_tax_lines(invoice_id: uuid.UUID, db: SessionDep, _: CurrentUserPayload):
    inv = await _get_invoice(db, invoice_id)
    lines = await _lines_of(db, invoice_id)
    return TaxLinesResponse(
        invoice_id=inv.id, tax_amount=inv.tax_amount, total_amount=inv.total_amount,
        lines=[TaxLineOut.model_validate(ln) for ln in lines],
    )


@router.put("/{invoice_id}/tax-lines", response_model=TaxLinesResponse)
async def replace_tax_lines(
    invoice_id: uuid.UUID,
    body: list[TaxLineIn],
    db: SessionDep,
    _: InvoiceEditDep,
):
    inv = await _get_invoice(db, invoice_id)
    if inv.status == "paid":
        raise HTTPException(status_code=409, detail="Cannot edit tax lines on a paid invoice")
    for ln in body:
        if ln.tax_amount < 0:
            raise HTTPException(status_code=422, detail="tax_amount cannot be negative")

    await db.execute(delete(InvoiceTaxLine).where(InvoiceTaxLine.invoice_id == invoice_id))
    db.add_all([
        InvoiceTaxLine(
            invoice_id=invoice_id, line_no=i + 1,
            tax_code=ln.tax_code, taxable_amount=ln.taxable_amount,
            tax_amount=ln.tax_amount, recoverable=ln.recoverable,
        )
        for i, ln in enumerate(body)
    ])
    # tax lines are the source of truth — derive the header
    inv.tax_amount = sum((ln.tax_amount for ln in body), Decimal("0"))
    inv.total_amount = inv.amount + inv.tax_amount
    await db.flush()

    lines = await _lines_of(db, invoice_id)
    return TaxLinesResponse(
        invoice_id=inv.id, tax_amount=inv.tax_amount, total_amount=inv.total_amount,
        lines=[TaxLineOut.model_validate(ln) for ln in lines],
    )
