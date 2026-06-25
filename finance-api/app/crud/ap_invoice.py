"""AP invoice CRUD — finance-owned, upserted from EPMS/OA by (source, source_invoice_id)."""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ap_invoice import ApInvoice, ApInvoiceTaxLine, VOID

_ZERO = Decimal("0")


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


async def _next_number(db: AsyncSession) -> str:
    today = date.today().strftime("%Y%m%d")
    like = f"AP-{today}-%"
    n = (await db.execute(
        select(func.count()).select_from(ApInvoice).where(ApInvoice.ap_invoice_number.like(like))
    )).scalar_one()
    return f"AP-{today}-{n + 1:04d}"


async def upsert(db: AsyncSession, *, source: str, source_invoice_id: uuid.UUID,
                 payload: dict, tax_lines: list[dict]) -> ApInvoice:
    """Create or update an AP invoice keyed on (source, source_invoice_id).
    payload keys: source_ref, vendor_id, vendor_name, vendor_invoice_number,
    amount, tax_amount, total_amount, currency, invoice_date, due_date, status,
    source_status, po_id, po_number. Tax lines are rebuilt each call."""
    inv = (await db.execute(
        select(ApInvoice).where(ApInvoice.source == source,
                                ApInvoice.source_invoice_id == source_invoice_id)
    )).scalar_one_or_none()

    fields = dict(
        source_ref=payload.get("source_ref"),
        vendor_id=payload.get("vendor_id"),
        vendor_name=payload.get("vendor_name"),
        vendor_invoice_number=payload.get("vendor_invoice_number"),
        amount=_q(payload.get("amount", 0)),
        tax_amount=_q(payload.get("tax_amount", 0)),
        total_amount=_q(payload.get("total_amount", 0)),
        currency=payload.get("currency", "CAD"),
        invoice_date=payload["invoice_date"],
        due_date=payload.get("due_date"),
        status=payload.get("status", "draft"),
        source_status=payload.get("source_status"),
        po_id=payload.get("po_id"),
        po_number=payload.get("po_number"),
    )

    if inv is None:
        inv = ApInvoice(
            ap_invoice_number=await _next_number(db),
            source=source, source_invoice_id=source_invoice_id, **fields,
        )
        db.add(inv)
    else:
        for k, v in fields.items():
            setattr(inv, k, v)
    await db.flush()

    await db.execute(sa_delete(ApInvoiceTaxLine).where(ApInvoiceTaxLine.invoice_id == inv.id))
    for t in tax_lines:
        db.add(ApInvoiceTaxLine(
            invoice_id=inv.id, line_no=t["line_no"], tax_code=t.get("tax_code"),
            taxable_base=_q(t.get("taxable_base", 0)), tax_amount=_q(t.get("tax_amount", 0)),
            recoverable=bool(t.get("recoverable", True)),
        ))
    await db.flush()
    return inv


async def list_invoices(db: AsyncSession, *, source: str | None = None,
                        vendor_id: uuid.UUID | None = None, status: str | None = None,
                        limit: int = 500) -> list[ApInvoice]:
    q = select(ApInvoice)
    if source:
        q = q.where(ApInvoice.source == source)
    if vendor_id:
        q = q.where(ApInvoice.vendor_id == vendor_id)
    if status:
        q = q.where(ApInvoice.status == status)
    return list((await db.execute(
        q.order_by(ApInvoice.invoice_date.desc()).limit(limit)
    )).scalars().all())


async def get(db: AsyncSession, invoice_id: uuid.UUID) -> ApInvoice | None:
    return (await db.execute(select(ApInvoice).where(ApInvoice.id == invoice_id))).scalar_one_or_none()


async def get_tax_lines(db: AsyncSession, invoice_id: uuid.UUID) -> list[ApInvoiceTaxLine]:
    return list((await db.execute(
        select(ApInvoiceTaxLine).where(ApInvoiceTaxLine.invoice_id == invoice_id)
        .order_by(ApInvoiceTaxLine.line_no)
    )).scalars().all())


async def set_void(db: AsyncSession, invoice_id: uuid.UUID) -> ApInvoice | None:
    inv = await get(db, invoice_id)
    if inv is None:
        return None
    inv.status = VOID
    await db.flush()
    return inv


async def open_items(db: AsyncSession, vendor_id: uuid.UUID | None = None,
                     limit: int = 500) -> list[ApInvoice]:
    from app.models.ap_invoice import OPEN_STATUSES
    q = select(ApInvoice).where(ApInvoice.status.in_(OPEN_STATUSES))
    if vendor_id:
        q = q.where(ApInvoice.vendor_id == vendor_id)
    return list((await db.execute(q.order_by(ApInvoice.due_date.nullslast()).limit(limit))).scalars().all())


async def aging(db: AsyncSession) -> list[dict]:
    from app.models.ap_invoice import OPEN_STATUSES
    rows = (await db.execute(
        select(ApInvoice).where(ApInvoice.status.in_(OPEN_STATUSES))
    )).scalars().all()
    today = date.today()
    buckets: dict[tuple, dict[str, Decimal]] = {}
    for r in rows:
        key = (r.vendor_id, r.vendor_name or "", r.currency)
        b = buckets.setdefault(key, {k: _ZERO for k in
                                     ("current", "d1_30", "d31_60", "d61_90", "d90_plus")})
        outstanding = _q(r.total_amount - r.paid_amount)
        overdue = (today - r.due_date).days if r.due_date is not None else 0
        slot = ("current" if overdue <= 0 else
                "d1_30" if overdue <= 30 else
                "d31_60" if overdue <= 60 else
                "d61_90" if overdue <= 90 else "d90_plus")
        b[slot] += outstanding
    return [
        {"vendor_id": str(vid) if vid else None, "vendor_name": vname, "currency": cur,
         "current": str(b["current"]), "d1_30": str(b["d1_30"]), "d31_60": str(b["d31_60"]),
         "d61_90": str(b["d61_90"]), "d90_plus": str(b["d90_plus"]),
         "total": str(sum(b.values(), _ZERO))}
        for (vid, vname, cur), b in sorted(buckets.items(), key=lambda kv: kv[0][1])
    ]
