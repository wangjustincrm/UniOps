"""Accounts Receivable spine (Phase c framework).

Mirrors AP: revenue recognition posts the receivable, receipts clear it, open
items / aging read the invoice table directly (event replay takes over at GL).
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._numbering import next_number
from app.crud.payment_execute import _stamp_account_codes, _stamp_fx
from app.models.ar import (
    OPEN_STATUSES, PAID, PARTIALLY_PAID, POSTED, ArInvoice, ArInvoiceTaxLine, ArReceipt,
)
from app.services.posting import emit_event

_ZERO = Decimal("0")


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


async def _next_number(db: AsyncSession, col, prefix: str) -> str:
    """PREFIX-YYYYMMDD-NNNN, sequential within the day.

    Delegates to the shared allocator (max-tail+1, immune to voided/deleted gaps,
    plus a per-prefix advisory lock for concurrency)."""
    today = date.today().strftime("%Y%m%d")
    return await next_number(db, col, f"{prefix}-{today}-", width=4)


# ── invoice creation ──────────────────────────────────────────────────────────────

async def create_invoice(db: AsyncSession, *, customer_id: uuid.UUID, customer_name: str,
                         invoice_date: date, due_date: date, currency: str,
                         lines_tax: list[dict], amount: Decimal,
                         invoice_number: str | None = None,
                         description: str | None = None) -> ArInvoice:
    """lines_tax: [{tax_code, taxable_base?, tax_amount}]. amount = pre-tax total."""
    tax_total = sum((_q(t.get("tax_amount", 0)) for t in lines_tax), _ZERO)
    amount = _q(amount)
    inv = ArInvoice(
        invoice_number=invoice_number or await _next_number(db, ArInvoice.invoice_number, "AR"),
        customer_id=customer_id, customer_name=customer_name,
        amount=amount, tax_amount=tax_total, total_amount=amount + tax_total,
        currency=currency, invoice_date=invoice_date, due_date=due_date,
        description=description,
    )
    db.add(inv)
    await db.flush()
    for i, t in enumerate(lines_tax, start=1):
        db.add(ArInvoiceTaxLine(
            invoice_id=inv.id, line_no=i, tax_code=t["tax_code"],
            taxable_base=_q(t.get("taxable_base", 0)), tax_amount=_q(t.get("tax_amount", 0)),
        ))
    await db.flush()
    return inv


# ── revenue recognition ─────────────────────────────────────────────────────────

async def post_invoice_revenue(db: AsyncSession, invoice_id: uuid.UUID) -> dict:
    """Emit the revenue event (debit AR / credit revenue / credit output_tax per
    tax_code). draft → posted. Idempotent on (ar_invoice, id, revenue)."""
    inv = (await db.execute(
        select(ArInvoice).where(ArInvoice.id == invoice_id)
    )).scalar_one_or_none()
    if inv is None:
        raise LookupError("AR invoice not found")
    if inv.status == "void":
        raise ValueError("Cannot post a void invoice")

    tax_lines = (await db.execute(
        select(ArInvoiceTaxLine).where(ArInvoiceTaxLine.invoice_id == invoice_id)
        .order_by(ArInvoiceTaxLine.line_no)
    )).scalars().all()

    lines: list[dict] = [
        {"line_role": "accounts_receivable", "debit": inv.total_amount,
         "partner_id": inv.customer_id, "partner_name": inv.customer_name, "currency": inv.currency},
        {"line_role": "revenue", "credit": inv.amount,
         "partner_id": inv.customer_id, "partner_name": inv.customer_name, "currency": inv.currency},
    ]
    if tax_lines:
        for t in tax_lines:
            if t.tax_amount > _ZERO:
                lines.append({"line_role": "output_tax", "credit": t.tax_amount,
                              "tax_code": t.tax_code, "currency": inv.currency})
    elif inv.tax_amount > _ZERO:
        lines.append({"line_role": "output_tax", "credit": inv.tax_amount,
                      "tax_code": None, "currency": inv.currency})

    event_id = await emit_event(
        db, source_service="finance", source_doc_type="ar_invoice",
        source_doc_id=inv.id, source_doc_number=inv.invoice_number,
        event_type="revenue",
        lines=await _stamp_fx(db, await _stamp_account_codes(db, lines), inv.invoice_date),
    )
    if inv.status == "draft":
        inv.status = POSTED
        inv.posted_at = datetime.now(timezone.utc)
    await db.flush()
    return {"invoice_id": inv.id, "posting_event_id": event_id,
            "already_posted": event_id is None, "status": inv.status}


# ── customer receipt ──────────────────────────────────────────────────────────────

async def record_receipt(db: AsyncSession, *, customer_id: uuid.UUID, customer_name: str,
                         amount: Decimal, currency: str, receipt_date: date,
                         invoice_id: uuid.UUID | None = None, method: str = "bank_transfer",
                         bank_account_id: uuid.UUID | None = None, reference: str | None = None,
                         recorded_by: uuid.UUID | None = None) -> dict:
    """Cash in: debit bank / credit AR. Applies to one invoice (on-account when
    invoice_id is None). Marks the invoice partially_paid / paid."""
    amount = _q(amount)
    if amount <= _ZERO:
        raise ValueError("Receipt amount must be positive")

    inv: ArInvoice | None = None
    if invoice_id is not None:
        inv = (await db.execute(
            select(ArInvoice).where(ArInvoice.id == invoice_id)
        )).scalar_one_or_none()
        if inv is None:
            raise LookupError("AR invoice not found")
        if inv.status not in OPEN_STATUSES:
            raise ValueError(f"Invoice status '{inv.status}' is not open for receipt")
        if inv.currency != currency:
            raise ValueError(f"Receipt currency {currency} != invoice currency {inv.currency}")

    receipt = ArReceipt(
        receipt_number=await _next_number(db, ArReceipt.receipt_number, "RCP"),
        customer_id=customer_id, customer_name=customer_name, invoice_id=invoice_id,
        amount=amount, currency=currency, receipt_date=receipt_date, method=method,
        bank_account_id=bank_account_id, reference=reference, recorded_by=recorded_by,
    )
    db.add(receipt)
    await db.flush()

    event_id = await emit_event(
        db, source_service="finance", source_doc_type="ar_receipt",
        source_doc_id=receipt.id, source_doc_number=receipt.receipt_number,
        event_type="receipt",
        lines=await _stamp_fx(db, await _stamp_account_codes(db, [
            {"line_role": "bank", "debit": amount, "currency": currency},
            {"line_role": "accounts_receivable", "credit": amount,
             "partner_id": customer_id, "partner_name": customer_name, "currency": currency},
        ]), receipt_date),
    )

    if inv is not None:
        inv.paid_amount = _q(inv.paid_amount + amount)
        inv.status = PAID if inv.paid_amount >= inv.total_amount else PARTIALLY_PAID

    await db.flush()
    return {"receipt_id": receipt.id, "receipt_number": receipt.receipt_number,
            "posting_event_id": event_id,
            "invoice_status": inv.status if inv else None}


# ── open items / aging ────────────────────────────────────────────────────────────

async def open_items(db: AsyncSession, customer_id: uuid.UUID | None = None,
                     limit: int = 200) -> list[ArInvoice]:
    q = select(ArInvoice).where(ArInvoice.status.in_(OPEN_STATUSES))
    if customer_id:
        q = q.where(ArInvoice.customer_id == customer_id)
    return list((await db.execute(q.order_by(ArInvoice.due_date).limit(limit))).scalars().all())


async def aging(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(
        select(ArInvoice).where(ArInvoice.status.in_(OPEN_STATUSES))
    )).scalars().all()
    today = date.today()
    buckets: dict[tuple, dict[str, Decimal]] = {}
    for r in rows:
        key = (r.customer_id, r.customer_name, r.currency)
        b = buckets.setdefault(key, {k: _ZERO for k in
                                     ("current", "d1_30", "d31_60", "d61_90", "d90_plus")})
        outstanding = _q(r.total_amount - r.paid_amount)
        overdue = (today - r.due_date).days
        slot = ("current" if overdue <= 0 else
                "d1_30" if overdue <= 30 else
                "d31_60" if overdue <= 60 else
                "d61_90" if overdue <= 90 else "d90_plus")
        b[slot] += outstanding
    return [
        {"customer_id": str(cid), "customer_name": cname, "currency": cur,
         "current": str(b["current"]), "d1_30": str(b["d1_30"]), "d31_60": str(b["d31_60"]),
         "d61_90": str(b["d61_90"]), "d90_plus": str(b["d90_plus"]),
         "total": str(sum(b.values(), _ZERO))}
        for (cid, cname, cur), b in sorted(buckets.items(), key=lambda kv: kv[0][1])
    ]
