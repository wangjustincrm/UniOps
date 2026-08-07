"""Payment batch (payment run) logic — Phase a A4."""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import payment_execute
from app.crud.payment_execute import PaymentPermissionError
from app.models.mirrors import ExpenseClaim, ExpenseInvoice, Invoice
from app.models.pa import PaymentApplication
from app.models.payment_batch import DRAFT, EXECUTED, PaymentBatch, PaymentBatchLine
from app.schemas.payment_execute import PaymentExecuteRequest


async def _next_batch_number(db: AsyncSession) -> str:
    # BP = Batch Payment (do NOT use PR — that collides with Purchase Request)
    today = date.today().strftime("%Y%m%d")
    prefix = f"BP-{today}-"
    n = (await db.execute(
        select(func.count()).select_from(PaymentBatch)
        .where(PaymentBatch.batch_number.like(f"{prefix}%"))
    )).scalar_one()
    return f"{prefix}{n + 1:04d}"


async def vendor_inv_no_map(db: AsyncSession,
                            pas: list[PaymentApplication]) -> dict[uuid.UUID, str]:
    """{pa.id: 'VINV-1, VINV-2'} — resolve each PA's invoice_ids to its vendor
    invoice number(s) in ONE batch query (a PA can reference several invoices;
    they join for display). PAs without invoices map to ''."""
    all_inv_ids: set[uuid.UUID] = set()
    per_pa: dict[uuid.UUID, list[uuid.UUID]] = {}
    for r in pas:
        ids: list[uuid.UUID] = []
        for iid in r.invoice_ids or []:
            try:
                u = uuid.UUID(str(iid))
            except (ValueError, TypeError):
                continue
            ids.append(u)
            all_inv_ids.add(u)
        per_pa[r.id] = ids

    inv_no_by_id: dict[uuid.UUID, str] = {}
    if all_inv_ids:
        inv_rows = (await db.execute(
            select(Invoice.id, Invoice.vendor_invoice_number)
            .where(Invoice.id.in_(all_inv_ids))
        )).all()
        inv_no_by_id = {iid: no for iid, no in inv_rows if no}

    out: dict[uuid.UUID, str] = {}
    for pid, ids in per_pa.items():
        nums: list[str] = []
        for u in ids:
            no = inv_no_by_id.get(u)
            if no and no not in nums:
                nums.append(no)
        out[pid] = ", ".join(nums)

    # Fallback (2026-07-23): an OA-created Direct PA's invoice_ids hold an
    # expense_invoices.id — a different table in a different id space than
    # epms's `invoices` — so the lookup above never matches for it, and the
    # group was blocked `missing_invoice_no` forever with no screen anywhere
    # able to fix it (see expense-api/app/api/v1/pa.py, which sets
    # invoice_ids=[str(body.invoice_id)] to that id). Resolved here via
    # expense_invoices.pa_id (set by expense-api on confirm), not by
    # re-interpreting invoice_ids against yet another table: that stays
    # correct even for historical rows whose invoice_ids values are not
    # reliably parseable as expense_invoices ids either, and it reads as
    # "ask the OA invoice which PA it belongs to" rather than "guess which
    # table this foreign id points into". Only attempted for PAs the epms
    # lookup resolved nothing for.
    unresolved = [pid for pid, nums in out.items() if not nums]
    if unresolved:
        rows = (await db.execute(
            select(ExpenseInvoice.pa_id, ExpenseInvoice.invoice_number)
            .where(ExpenseInvoice.pa_id.in_(unresolved))
        )).all()
        by_pa: dict[uuid.UUID, list[str]] = {}
        for pa_id, no in rows:
            if not no:
                continue
            nums = by_pa.setdefault(pa_id, [])
            if no not in nums:
                nums.append(no)
        for pid, nos in by_pa.items():
            out[pid] = ", ".join(nos)
    return out


async def vendor_inv_no_for_lines(db: AsyncSession,
                                  lines: list["PaymentBatchLine"]) -> dict[uuid.UUID, str]:
    """{line.doc_id: vendor invoice number(s)} for PA / Direct-PA batch lines.
    Resolved at read time (lines snapshot doc_number/amount, not the invoice)."""
    pa_ids = [ln.doc_id for ln in lines if ln.doc_kind in ("pa", "pa_dir")]
    if not pa_ids:
        return {}
    pas = (await db.execute(
        select(PaymentApplication).where(PaymentApplication.id.in_(pa_ids))
    )).scalars().all()
    return await vendor_inv_no_map(db, pas)


async def list_due(db: AsyncSession, currency: str | None = None) -> list[dict]:
    """Approved PAs and approved expense claims awaiting payment — pickable rows."""
    pq = select(PaymentApplication).where(PaymentApplication.status == "approved")
    if currency:
        pq = pq.where(PaymentApplication.currency == currency)
    pas = (await db.execute(pq.order_by(PaymentApplication.submitted_at))).scalars().all()

    cq = select(ExpenseClaim).where(ExpenseClaim.status == "approved")
    if currency:
        cq = cq.where(ExpenseClaim.currency == currency)
    claims = (await db.execute(cq.order_by(ExpenseClaim.created_at))).scalars().all()

    inv_no = await vendor_inv_no_map(db, pas)
    rows = [
        {"doc_kind": "pa_dir" if r.po_id is None else "pa",
         "doc_id": str(r.id), "doc_number": r.pa_number,
         "payee": r.vendor_name, "vendor_inv_no": inv_no.get(r.id, ""),
         "amount": str(r.payment_amount), "currency": r.currency}
        for r in pas
    ]
    rows += [
        {"doc_kind": "expense_claim", "doc_id": str(c.id), "doc_number": c.claim_number,
         "payee": c.employee_name, "vendor_inv_no": "",
         "amount": str(c.total_amount), "currency": c.currency}
        for c in claims
    ]
    return rows


async def create_batch(db: AsyncSession, *, docs: list[tuple[str, uuid.UUID]],
                       payment_method: str, batch_date: date,
                       created_by: uuid.UUID) -> PaymentBatch:
    """Snapshot selected approved PAs and/or claims into a draft batch. Raises
    ValueError on empty / not-found / non-approved / mixed-currency selection."""
    if not docs:
        raise ValueError("Select at least one document to pay")
    pa_ids = [d_id for kind, d_id in docs if kind in ("pa", "pa_dir")]
    claim_ids = [d_id for kind, d_id in docs if kind == "expense_claim"]

    pas = (await db.execute(
        select(PaymentApplication).where(PaymentApplication.id.in_(pa_ids))
    )).scalars().all() if pa_ids else []
    claims = (await db.execute(
        select(ExpenseClaim).where(ExpenseClaim.id.in_(claim_ids))
    )).scalars().all() if claim_ids else []

    found_pa = {p.id for p in pas}
    found_claim = {c.id for c in claims}
    missing = ([str(d) for d in pa_ids if d not in found_pa]
               + [str(d) for d in claim_ids if d not in found_claim])
    if missing:
        raise ValueError(f"Documents not found: {missing}")
    not_approved = ([p.pa_number for p in pas if p.status != "approved"]
                    + [c.claim_number for c in claims if c.status != "approved"])
    if not_approved:
        raise ValueError(f"Not in approved status: {not_approved}")
    currencies = {p.currency for p in pas} | {c.currency for c in claims}
    if len(currencies) > 1:
        raise ValueError(f"A batch must be single-currency; got {sorted(currencies)}")

    total = (sum((p.payment_amount for p in pas), Decimal("0"))
             + sum((c.total_amount for c in claims), Decimal("0")))
    batch = PaymentBatch(
        batch_number=await _next_batch_number(db),
        batch_date=batch_date, status=DRAFT,
        currency=next(iter(currencies)), total=total,
        payment_method=payment_method, created_by=created_by,
    )
    db.add(batch)
    await db.flush()
    for p in pas:
        # `amount` here is the gross payable snapshotted when the batch is
        # built — it is what is owed, not what will actually be sent. Vendor
        # credits net the cash at execution time (payment_execute.execute),
        # after this line is written; the netted figure shows up in the
        # payment preview and is recorded on the payment_record, not here.
        db.add(PaymentBatchLine(
            batch_id=batch.id,
            doc_kind="pa_dir" if p.po_id is None else "pa",
            doc_id=p.id, doc_number=p.pa_number, amount=p.payment_amount,
        ))
    for c in claims:
        db.add(PaymentBatchLine(
            batch_id=batch.id, doc_kind="expense_claim",
            doc_id=c.id, doc_number=c.claim_number, amount=c.total_amount,
        ))
    await db.flush()
    return batch


async def execute_batch(db: AsyncSession, batch: PaymentBatch, user: dict,
                        bearer_token: str | None,
                        bank_account_id: uuid.UUID | None = None) -> PaymentBatch:
    """Run every line through the unified executor under one batch tag. A line
    failure is isolated (savepoint rollback) and recorded; the rest proceed.
    bank_account_id (chosen at execute) funds every line and is recorded on the
    batch + each payment_record."""
    if batch.status != DRAFT:
        raise ValueError(f"Batch is already {batch.status}")
    # validate the funding bank up-front (currency must match the batch)
    bank = await payment_execute._resolve_bank(db, bank_account_id, batch.currency)
    if bank is not None:
        batch.bank_account_id = bank.id
    lines = (await db.execute(
        select(PaymentBatchLine).where(PaymentBatchLine.batch_id == batch.id)
    )).scalars().all()

    for ln in lines:
        if ln.status == "paid":
            continue
        try:
            async with db.begin_nested():  # savepoint per line
                result = await payment_execute.execute(
                    db,
                    PaymentExecuteRequest(
                        doc_kind=ln.doc_kind, doc_id=ln.doc_id,
                        payment_date=batch.batch_date, payment_method=batch.payment_method,
                        bank_account_id=bank_account_id,
                    ),
                    user, bearer_token=bearer_token, batch_id=batch.id,
                )
            ln.status = "paid"
            ln.payment_record_id = result.payment_record_id
            ln.error = None
        except (PaymentPermissionError, LookupError, ValueError) as e:
            ln.status = "failed"
            ln.error = str(e)[:500]

    batch.status = EXECUTED
    from datetime import datetime, timezone
    batch.executed_at = datetime.now(timezone.utc)
    await db.flush()
    return batch
