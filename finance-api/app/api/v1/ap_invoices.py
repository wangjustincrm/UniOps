"""AP Invoice API — finance-owned AP invoices upserted from EPMS/OA."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.coa import _require_manage
from app.core.deps import _FINANCE_ROLES, CurrentUser
from app.core.list_sort import SortDir, lower, order_by
from app.crud import ap_invoice as crud
from app.crud import payment_execute
from app.crud.ap_accrual import post_invoice_accrual, reverse_invoice_accrual
from app.db.base import get_db
from app.models.ap_invoice import OPEN_STATUSES, POSTED, VOID, ApInvoice

router = APIRouter(prefix="/ap", tags=["accounts-payable-invoices"])

# GET /ap/invoices click-to-sort whitelist — each key sorts what that column of
# the AP workbench shows (finance/src/pages/finance/AccountsPayablePage.tsx).
# "outstanding" is shown only for open invoices (dash otherwise), so it is NULL
# for the rest and those sink to the bottom.
_INVOICE_SORT = {
    "ap_invoice_number": ApInvoice.ap_invoice_number,
    "source": ApInvoice.source,
    "vendor_name": lower(ApInvoice.vendor_name),
    "vendor_invoice_number": lower(ApInvoice.vendor_invoice_number),
    "invoice_date": ApInvoice.invoice_date,
    "due_date": ApInvoice.due_date,
    "total_amount": ApInvoice.total_amount,
    "outstanding": case(
        (ApInvoice.status.in_(OPEN_STATUSES),
         ApInvoice.total_amount - ApInvoice.paid_amount),
        else_=None),
    "status": ApInvoice.status,
    "nc_bill_no": crud.NC_DOC_SORT,   # pushed bill, else hand-entered match
}


async def _require_finance_role(db: AsyncSession, user: dict) -> None:
    """Any finance role (primary JWT role or an additional identity role
    assignment) may push AP to NC — it is a routine AP handoff to NC65,
    NOT chart-of-accounts management. This mirrors payments._authorize_read
    ("who may see finance data", `_FINANCE_ROLES`); the export was wrongly
    gated on coa._require_manage (finance.coa.manage → system_admin /
    finance_manager only), which 403'd every ap_clerk. CurrentUser only
    decodes the JWT, so this gate is what keeps a bare token (an OA-only
    employee with no finance role) out of /ap/nc-push."""
    role = user.get("role", "")
    if role in _FINANCE_ROLES:
        return
    try:
        user_id = uuid.UUID(str(user.get("sub", "")))
    except ValueError:
        raise HTTPException(status_code=403, detail="Insufficient role to push AP invoices to NC")
    codes = await payment_execute._user_role_codes(db, user_id, role)
    if not codes & _FINANCE_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role to push AP invoices to NC")


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
    nc_exported_at: datetime | None = None
    nc_bill_no: str | None = None
    nc_push_status: str | None = None
    nc_push_error: str | None = None
    nc_pushed_at: datetime | None = None
    # NC payable bills holding this AP (push result + NC AP mirror match);
    # filled by the list endpoint only.
    nc_docs: list[dict] = []

    @field_validator("amount", "tax_amount", "total_amount", "paid_amount", mode="before")
    @classmethod
    def _s(cls, v): return str(v)


@router.post("/invoices", response_model=InvoiceOut | None)
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
        out = InvoiceOut.model_validate(inv)
        if await crud.void_for_deleted_source(db, inv) == "deleted":
            await db.commit()
            return out
        await db.commit()
        return inv

    try:
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
    except IntegrityError as e:
        await db.rollback()
        if "uq_ap_invoices_vendor_invno" in str(e.orig):
            raise HTTPException(status_code=409, detail=(
                "duplicate vendor invoice: this vendor already has an invoice "
                "with the same vendor invoice number"))
        raise
    return inv


@router.get("/invoices")
async def list_invoices(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        source: str | None = Query(default=None),
                        vendor_id: uuid.UUID | None = Query(default=None),
                        status: str | None = Query(default=None),
                        q: str | None = Query(default=None),
                        exported: bool | None = Query(default=None),
                        in_nc: bool | None = Query(default=None),
                        limit: int = Query(default=50, le=200),
                        offset: int = Query(default=0, ge=0),
                        sort: str | None = Query(default=None),
                        dir: SortDir = Query(default="asc")):
    order = order_by(sort, dir, _INVOICE_SORT, tiebreak=(ApInvoice.id,))
    total, rows = await crud.list_invoices(
        db, source=source, vendor_id=vendor_id, status=status, q=q,
        exported=exported, in_nc=in_nc, limit=limit, offset=offset, order=order)
    from app.services.nc_push import nc_docs_for
    docs = await nc_docs_for(db, rows)
    items = []
    for r in rows:
        out = InvoiceOut.model_validate(r)
        found = docs.get(r.id, [])
        # The push result is authoritative for our own bill; the mirror may not
        # have synced it yet (hourly) — show it as pushed until it does.
        if r.nc_bill_no and not any(d["bill_no"] == r.nc_bill_no for d in found):
            found = [{"bill_no": r.nc_bill_no, "state": "pushed", "pushed": True,
                      "bill_date": None, "money": None, "currency": None,
                      "hyphen_match": False, "nc_invoice_no": None}] + found
        out.nc_docs = found
        items.append(out)
    return {"total": total, "items": items}


class NcPushIn(BaseModel):
    ap_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


@router.post("/nc-push")
async def nc_push(body: NcPushIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Push the chosen AP invoices to NC as saved payable bills (XChange).
    The person who clicks is the NC bill maker. Returns one outcome per AP;
    a refused or failed AP does not stop the others."""
    from app.services import nc_push as svc

    await _require_finance_role(db, user)
    if not svc.xchange_configured():
        raise HTTPException(status_code=503, detail="Push to NC is not configured on this server")
    outcomes = await svc.push_invoices(db, body.ap_ids, uuid.UUID(user["sub"]))
    await db.commit()
    return {"results": [{"ap_id": str(o.ap_id), "ap_number": o.ap_number, "ok": o.ok,
                         "message": o.message, "nc_bill_no": o.nc_bill_no} for o in outcomes]}


@router.get("/invoices/{invoice_id}/nc-push-log")
async def nc_push_log(invoice_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    from app.models.nc_push import NcPushLog

    await _require_finance_role(db, user)
    rows = (await db.execute(select(NcPushLog).where(NcPushLog.ap_invoice_id == invoice_id)
                             .order_by(NcPushLog.pushed_at.desc()).limit(50))).scalars().all()
    return [{"pushed_at": r.pushed_at.isoformat(), "pushed_by": str(r.pushed_by) if r.pushed_by else None,
             "ok": r.ok, "result_code": r.result_code, "nc_bill_no": r.nc_bill_no,
             "nc_bill_pk": r.nc_bill_pk, "message": r.message} for r in rows]


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: uuid.UUID, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    inv = await crud.get(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="AP invoice not found")
    tax = await crud.get_tax_lines(db, invoice_id)
    return {"invoice": InvoiceOut.model_validate(inv).model_dump(),
            "tax_lines": [{"line_no": t.line_no, "tax_code": t.tax_code,
                           "taxable_base": str(t.taxable_base), "tax_amount": str(t.tax_amount),
                           "recoverable": t.recoverable} for t in tax]}


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceOut)
async def void_invoice(invoice_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    inv = await crud.set_void(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="AP invoice not found")
    # a void must not leave its accrual on the books
    await reverse_invoice_accrual(db, invoice_id)
    await db.commit()
    return inv
