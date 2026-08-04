"""Expense invoice endpoints — client-side OCR, server stores metadata + dedup."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import BearerTokenDep, CurrentUserDep, SessionDep
from app.models.epms_mirrors import EpmsInvoice, EpmsVendor
from app.models.invoice import ExpenseInvoice, ExpenseInvoiceLine
from app.schemas.invoice import InvoiceResponse, InvoiceUpdate, VendorSuggestion
from app.services import finance_sync

router = APIRouter(prefix="/invoices", tags=["invoices"])


# ── Request schema (JSON, OCR done client-side) ───────────────────────────────

class InvoiceLineCreate(BaseModel):
    line_number: int
    description: str
    quantity: Decimal = Decimal("1")
    unit_price: Decimal = Decimal("0")
    amount: Decimal
    tax_amount: Decimal = Decimal("0")
    unit: str | None = None


class InvoiceCreate(BaseModel):
    """All fields come from client-side OCR. Server validates dedup and persists."""
    file_name: str
    file_mime_type: str
    file_size_bytes: int

    invoice_number: Optional[str] = None
    vendor_id: Optional[uuid.UUID] = None
    vendor_name: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    currency: str = "CAD"
    subtotal: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")

    lines: list[InvoiceLineCreate] = []


# ── Dedup helper ──────────────────────────────────────────────────────────────

async def _check_dedup(
    db: SessionDep,
    vendor_id: uuid.UUID | None,
    invoice_number: str | None,
    exclude_id: uuid.UUID | None = None,
) -> dict | None:
    """Uniqueness check: (vendor_id, invoice_number) must be unique across OA + EPMS.
    Skipped only when both vendor_id and invoice_number are absent."""
    if not vendor_id and not invoice_number:
        return None                     # nothing to check — both missing
    if not vendor_id or not invoice_number:
        return None                     # can't enforce vendor-scoped uniqueness without both

    inv_num = invoice_number.strip()

    # 1. Check OA expense_invoices
    q = select(ExpenseInvoice).where(
        ExpenseInvoice.vendor_id == vendor_id,
        ExpenseInvoice.invoice_number == inv_num,
    )
    if exclude_id:
        q = q.where(ExpenseInvoice.id != exclude_id)
    oa = (await db.execute(q.limit(1))).scalar_one_or_none()
    if oa:
        return {
            "source": "oa",
            "invoice_id": str(oa.id),
            "pa_number": oa.pa_number,
            "document_ref": oa.pa_number or str(oa.id),
        }

    # 2. Check EPMS invoices
    epms = (await db.execute(
        select(EpmsInvoice).where(
            EpmsInvoice.vendor_id == vendor_id,
            EpmsInvoice.vendor_invoice_number == inv_num,
        ).limit(1)
    )).scalar_one_or_none()
    if epms:
        return {
            "source": "epms",
            "invoice_id": str(epms.id),
            "document_ref": epms.internal_ref,
        }

    return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(body: InvoiceCreate, db: SessionDep, user: CurrentUserDep, token: BearerTokenDep):
    """Save OCR-extracted invoice metadata. Performs cross-table dedup check."""
    user_id = uuid.UUID(user["sub"])

    # Cross-table dedup
    dup = await _check_dedup(db, body.vendor_id, body.invoice_number)
    if dup:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Invoice {body.invoice_number} from this vendor already recorded",
                "duplicate": dup,
            },
        )

    inv = ExpenseInvoice(
        file_name=body.file_name,
        file_mime_type=body.file_mime_type,
        file_size_bytes=body.file_size_bytes,
        invoice_number=body.invoice_number,
        vendor_id=body.vendor_id,
        vendor_name=body.vendor_name,
        invoice_date=body.invoice_date,
        due_date=body.due_date,
        currency=body.currency,
        subtotal=body.subtotal,
        tax_amount=body.tax_amount,
        total_amount=body.total_amount,
        status="reviewed",   # client already reviewed OCR results
        confirmed_by=user_id,
        confirmed_at=datetime.now(timezone.utc),
        low_confidence_fields=[],
        created_by=user_id,
    )
    db.add(inv)
    await db.flush()

    for li in body.lines:
        db.add(ExpenseInvoiceLine(
            invoice_id=inv.id,
            line_number=li.line_number,
            description=li.description,
            quantity=li.quantity,
            unit_price=li.unit_price,
            amount=li.amount,
            tax_amount=li.tax_amount,
            unit=li.unit,
        ))

    await db.flush()
    await db.refresh(inv, ["lines"])
    await finance_sync.sync_ap_invoice(db, inv, token)
    return InvoiceResponse.model_validate(inv)


@router.get("/check-duplicate")
async def check_duplicate(
    db: SessionDep,
    _: CurrentUserDep,
    vendor_id: uuid.UUID | None = None,
    invoice_number: str | None = None,
):
    """Non-destructive dedup pre-check used by the OA Direct PA flow.

    Lets the client warn about a duplicate (vendor_id, invoice_number) right after
    the invoice is uploaded/reviewed — instead of only failing at submit time.
    Returns {"duplicate": <info|null>}.
    """
    dup = await _check_dedup(db, vendor_id, invoice_number)
    return {"duplicate": dup}


def _can_view_invoice(inv, user_id: uuid.UUID, role: str) -> bool:
    from app.api.v1.expenses import _CAN_PAY
    return inv.created_by == user_id or role == "system_admin" or role in _CAN_PAY


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(invoice_id: uuid.UUID, db: SessionDep, user: CurrentUserDep):
    inv = await db.get(ExpenseInvoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if not _can_view_invoice(inv, uuid.UUID(user["sub"]), user.get("role", "")):
        raise HTTPException(status_code=403, detail="Not authorized to view this invoice")
    await db.refresh(inv, ["lines"])
    return InvoiceResponse.model_validate(inv)


@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceUpdate,
    db: SessionDep,
    user: CurrentUserDep,
    token: BearerTokenDep,
):
    inv = await db.get(ExpenseInvoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status == "used":
        raise HTTPException(status_code=409, detail="Invoice already used in a PA")

    new_vendor_id = body.vendor_id if body.vendor_id is not None else inv.vendor_id
    new_invoice_number = body.invoice_number if body.invoice_number is not None else inv.invoice_number

    if (new_vendor_id != inv.vendor_id or new_invoice_number != inv.invoice_number):
        dup = await _check_dedup(db, new_vendor_id, new_invoice_number, exclude_id=inv.id)
        if dup:
            raise HTTPException(status_code=409, detail={
                "message": f"Invoice {new_invoice_number} from this vendor already recorded",
                "duplicate": dup,
            })

    for field in ("invoice_number", "vendor_id", "vendor_name", "invoice_date",
                  "due_date", "currency", "subtotal", "tax_amount", "total_amount"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(inv, field, val)

    if body.confirmed:
        inv.status = "reviewed"
        inv.confirmed_by = uuid.UUID(user["sub"])
        inv.confirmed_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(inv, ["lines"])
    await finance_sync.sync_ap_invoice(db, inv, token)
    return InvoiceResponse.model_validate(inv)


@router.get("/{invoice_id}/vendor-suggestions", response_model=list[VendorSuggestion])
async def vendor_suggestions(invoice_id: uuid.UUID, q: str, db: SessionDep, user: CurrentUserDep):
    inv = await db.get(ExpenseInvoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if not _can_view_invoice(inv, uuid.UUID(user["sub"]), user.get("role", "")):
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.execute(
        select(EpmsVendor).where(EpmsVendor.name.ilike(f"%{q}%")).limit(8)
    )
    return [VendorSuggestion(id=v.id, name=v.name, code=v.code) for v in result.scalars()]
