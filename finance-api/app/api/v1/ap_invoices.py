"""AP Invoice API — finance-owned AP invoices upserted from EPMS/OA."""
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.coa import _require_manage
from app.core.deps import CurrentUser
from app.crud import ap_invoice as crud
from app.crud.ap_accrual import has_postings, post_invoice_accrual, reverse_invoice_accrual
from app.db.base import get_db
from app.models.ap_invoice import POSTED, VOID

router = APIRouter(prefix="/ap/invoices", tags=["accounts-payable-invoices"])


class TaxLineIn(BaseModel):
    line_no: int
    tax_code: str | None = None
    taxable_base: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    recoverable: bool = True


class UpsertIn(BaseModel):
    source: str = Field(pattern="^(epms|oa)$")
    source_invoice_id: uuid.UUID
    source_ref: str | None = None
    vendor_id: uuid.UUID | None = None
    vendor_name: str | None = None
    vendor_invoice_number: str | None = None
    amount: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")
    currency: str = "CAD"
    invoice_date: date
    due_date: date | None = None
    status: str = "draft"
    source_status: str | None = None
    po_id: uuid.UUID | None = None
    po_number: str | None = None
    tax_lines: list[TaxLineIn] = []


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ap_invoice_number: str
    source: str
    source_invoice_id: uuid.UUID
    source_ref: str | None
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    vendor_invoice_number: str | None
    amount: str
    tax_amount: str
    total_amount: str
    paid_amount: str
    currency: str
    invoice_date: date
    due_date: date | None
    status: str
    source_status: str | None
    po_id: uuid.UUID | None
    po_number: str | None

    @field_validator("amount", "tax_amount", "total_amount", "paid_amount", mode="before")
    @classmethod
    def _s(cls, v): return str(v)


@router.post("", response_model=InvoiceOut | None)
async def upsert_invoice(body: UpsertIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    # Internal sync ingestion: EPMS/OA push their own invoices carrying the end
    # user's token (who created/matched the invoice but may lack finance-manage
    # rights). Auth (CurrentUser) is sufficient here — same as /ap/post-invoice.
    # No _require_manage (that gate is for human AP admins; void still uses it).
    if body.status == VOID:
        # Source deleted its invoice. An AP row that never touched the GL and
        # was never paid is just noise — remove it. A posted one stays for the
        # audit trail, voided, with its accrual reversed out of the GL.
        inv = await crud.get_by_source(db, source=body.source,
                                       source_invoice_id=body.source_invoice_id)
        if inv is None:
            return None
        if not await has_postings(db, inv.id) and inv.paid_amount == Decimal("0"):
            out = InvoiceOut.model_validate(inv)
            await crud.delete_invoice(db, inv)
            await db.commit()
            return out
        inv.status = VOID
        await reverse_invoice_accrual(db, inv.id)
        await db.commit()
        return inv

    inv = await crud.upsert(
        db, source=body.source, source_invoice_id=body.source_invoice_id,
        payload=body.model_dump(exclude={"source", "source_invoice_id", "tax_lines"}),
        tax_lines=[t.model_dump() for t in body.tax_lines],
    )
    if inv.status == POSTED:
        try:
            await post_invoice_accrual(db, inv.id)
        except ValueError:
            pass
    await db.commit()
    return inv


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        source: str | None = Query(default=None),
                        vendor_id: uuid.UUID | None = Query(default=None),
                        status: str | None = Query(default=None),
                        limit: int = Query(default=500, le=2000)):
    return await crud.list_invoices(db, source=source, vendor_id=vendor_id, status=status, limit=limit)


@router.get("/{invoice_id}")
async def get_invoice(invoice_id: uuid.UUID, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    inv = await crud.get(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="AP invoice not found")
    tax = await crud.get_tax_lines(db, invoice_id)
    return {"invoice": InvoiceOut.model_validate(inv).model_dump(),
            "tax_lines": [{"line_no": t.line_no, "tax_code": t.tax_code,
                           "taxable_base": str(t.taxable_base), "tax_amount": str(t.tax_amount),
                           "recoverable": t.recoverable} for t in tax]}


@router.post("/{invoice_id}/void", response_model=InvoiceOut)
async def void_invoice(invoice_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    inv = await crud.set_void(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="AP invoice not found")
    # a void must not leave its accrual on the books
    await reverse_invoice_accrual(db, invoice_id)
    await db.commit()
    return inv
