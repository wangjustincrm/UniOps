"""Payment batch (payment run) logic — Phase a A4."""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plant_time import plant_today
from app.crud import payment_execute
from app.crud._numbering import next_number
from app.crud.payment_execute import PaymentPermissionError
from app.models.mirrors import ExpenseClaim, ExpenseInvoice, Invoice
from app.models.pa import PaymentApplication
from app.models.payment_batch import CANCELLED, DRAFT, EXECUTED, PaymentBatch, PaymentBatchLine
from app.schemas.payment_execute import PaymentExecuteRequest
from app.services import nc_invoice_paid


async def _next_batch_number(db: AsyncSession) -> str:
    # BP = Batch Payment (do NOT use PR — that collides with Purchase Request)
    # Date = the plant's day (Company Settings → Time Zone). Max-tail+1 under a
    # per-prefix lock (shared allocator), not count+1: a count reissues a taken
    # number whenever the day's sequence has a gap, and two concurrent runs
    # would read the same count.
    today = plant_today().strftime("%Y%m%d")
    return await next_number(db, PaymentBatch.batch_number, f"BP-{today}-", width=4)


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


async def invoice_numbers(db: AsyncSession, invoice_ids: list) -> dict[str, str]:
    """{invoice id: vendor invoice number} for the invoices one payment covers."""
    ids = {uuid.UUID(str(i)) for i in invoice_ids}
    if not ids:
        return {}
    rows = (await db.execute(
        select(Invoice.id, Invoice.vendor_invoice_number).where(Invoice.id.in_(ids))
    )).all()
    return {str(i): no for i, no in rows if no}


def join_invoice_numbers(invoice_ids: list, numbers: dict[str, str]) -> str:
    """The vendor invoice numbers of a split payment, in the payment's order."""
    return ", ".join(numbers[str(i)] for i in invoice_ids if str(i) in numbers)


async def epms_invoice_refs(db: AsyncSession, pas: list[PaymentApplication],
                            paid: dict[uuid.UUID, tuple[set[str], Decimal]],
                            drafts: dict[uuid.UUID, dict[str, list[str]]],
                            ) -> dict[uuid.UUID, dict]:
    """{pa.id: {"invoices": [...], "due_date": iso | None, "splittable": bool}}
    for the due list: the epms invoices a PA settles, so each vendor invoice
    number can open its EPMS detail page, and the earliest due date among those
    still unpaid (invoices on one PA fall due within 7 days of each other, so
    the earliest is the one that matters for the run). `splittable` says the
    invoices can be picked one by one (payment_execute.split_amounts). OA Direct
    PAs' invoice_ids are expense_invoices ids (see vendor_inv_no_map) and OA no
    longer has an invoice detail page, so they get no links — the row falls back
    to the plain vendor_inv_no text."""
    per_pa: dict[uuid.UUID, list[uuid.UUID]] = {}
    for r in pas:
        ids: list[uuid.UUID] = []
        for iid in r.invoice_ids or []:
            try:
                ids.append(uuid.UUID(str(iid)))
            except (ValueError, TypeError):
                continue
        per_pa[r.id] = ids
    all_ids = {u for ids in per_pa.values() for u in ids}
    by_id: dict[uuid.UUID, tuple[str | None, date, Decimal]] = {}
    if all_ids:
        rows = (await db.execute(
            select(Invoice.id, Invoice.vendor_invoice_number, Invoice.due_date,
                   Invoice.total_amount)
            .where(Invoice.id.in_(all_ids))
        )).all()
        by_id = {iid: (no, due, total) for iid, no, due, total in rows}

    pa_by_id = {r.id: r for r in pas}
    out: dict[uuid.UUID, dict] = {}
    for pid, ids in per_pa.items():
        paid_ids = paid.get(pid, (set(), None))[0]
        in_drafts = drafts.get(pid, {})
        invoices: list[dict] = []
        dues: list[date] = []
        for u in dict.fromkeys(ids):
            if u not in by_id:
                continue
            no, due, total = by_id[u]
            is_paid = str(u) in paid_ids
            invoices.append({"id": str(u), "number": no or "", "amount": str(total),
                             "due_date": due.isoformat() if due else None,
                             "paid": is_paid,
                             "in_draft_batches": in_drafts.get(str(u), [])})
            if due and not is_paid:
                dues.append(due)
        totals = {str(u): by_id[u][2] for u in ids if u in by_id}
        out[pid] = {"invoices": invoices,
                    "due_date": min(dues).isoformat() if dues else None,
                    "splittable": payment_execute.split_amounts(pa_by_id[pid], totals) is not None}
    return out


async def vendor_inv_no_for_lines(db: AsyncSession,
                                  lines: list["PaymentBatchLine"]) -> dict[uuid.UUID, str]:
    """{line.doc_id: vendor invoice number(s)} for PA / Direct-PA batch lines.
    Resolved at read time (lines snapshot doc_number/amount, not the invoice).
    A line paying part of a PA shows only the invoices it pays."""
    pa_ids = [ln.doc_id for ln in lines if ln.doc_kind in ("pa", "pa_dir")]
    if not pa_ids:
        return {}
    pas = (await db.execute(
        select(PaymentApplication).where(PaymentApplication.id.in_(pa_ids))
    )).scalars().all()
    out = await vendor_inv_no_map(db, pas)
    split = [ln for ln in lines if ln.invoice_ids]
    numbers = await invoice_numbers(db, [i for ln in split for i in ln.invoice_ids])
    for ln in split:
        out[ln.doc_id] = join_invoice_numbers(ln.invoice_ids, numbers)
    return out


async def list_due(db: AsyncSession, currency: str | None = None) -> list[dict]:
    """Approved PAs and approved expense claims awaiting payment — pickable rows."""
    pq = select(PaymentApplication).where(PaymentApplication.status == "approved")
    if currency:
        pq = pq.where(PaymentApplication.currency == currency)
    pas = (await db.execute(pq.order_by(PaymentApplication.submitted_at))).scalars().all()

    # A Travel Application is approved like a claim but pays nothing (total 0)
    # — leaving it in made it a pickable $0 "Claim" row in every run.
    cq = select(ExpenseClaim).where(
        ExpenseClaim.status == "approved",
        ExpenseClaim.claim_type.notin_(payment_execute.NON_PAYABLE_CLAIM_TYPES))
    if currency:
        cq = cq.where(ExpenseClaim.currency == currency)
    claims = (await db.execute(cq.order_by(ExpenseClaim.created_at))).scalars().all()

    inv_no = await vendor_inv_no_map(db, pas)
    in_drafts, inv_drafts = await draft_batches_by_doc(
        db, [r.id for r in pas] + [c.id for c in claims], pas)
    paid = await payment_execute.pa_split_payments(db, [r.id for r in pas])
    refs = await epms_invoice_refs(db, pas, paid, inv_drafts)
    # What NC's books say about each invoice (nc_invoice_paid): a PA NC has
    # already paid but nobody closed is still pickable here — the column is
    # there so it is not paid twice.
    nc = await nc_invoice_paid.invoice_status(
        db, [inv["id"] for ref in refs.values() for inv in ref["invoices"]])
    for ref in refs.values():
        for inv in ref["invoices"]:
            inv["nc"] = nc[inv["id"]]
        states = [inv["nc"]["status"] for inv in ref["invoices"]]
        ref["nc_paid"] = nc_invoice_paid.rollup(states)
        ref["nc_paid_count"] = sum(s in nc_invoice_paid.SETTLED for s in states)
    rows = [
        {"doc_kind": "pa_dir" if r.is_direct else "pa",
         "doc_id": str(r.id), "doc_number": r.pa_number,
         "payee": r.vendor_name, "vendor_inv_no": inv_no.get(r.id, ""),
         "invoices": refs[r.id]["invoices"], "due_date": refs[r.id]["due_date"],
         "splittable": refs[r.id]["splittable"],
         "nc_paid": refs[r.id]["nc_paid"], "nc_paid_count": refs[r.id]["nc_paid_count"],
         # What is still owed; the PA's own total differs once part of it is paid.
         "amount": str(r.payment_amount - paid.get(r.id, (None, Decimal("0")))[1]),
         "payment_amount": str(r.payment_amount), "currency": r.currency,
         "claim_type": None,
         # po | agreement | freight | direct — lets the UI tell a Freight PA
         # (doc_kind 'pa', EPMS-owned) apart from a PO/agreement PA.
         "pa_source": r.source,
         "in_draft_batches": in_drafts.get(r.id, []),
         "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None}
        for r in pas
    ]
    rows += [
        {"doc_kind": "expense_claim", "doc_id": str(c.id), "doc_number": c.claim_number,
         "payee": c.employee_name, "vendor_inv_no": "",
         "invoices": [], "due_date": None, "splittable": False,
         "nc_paid": None, "nc_paid_count": 0,
         "amount": str(c.total_amount), "payment_amount": str(c.total_amount),
         "currency": c.currency,
         "claim_type": c.claim_type,
         "pa_source": None,
         "in_draft_batches": in_drafts.get(c.id, []),
         "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None}
        for c in claims
    ]
    return rows


async def draft_batches_by_doc(
    db: AsyncSession, doc_ids: list[uuid.UUID], pas: list[PaymentApplication],
) -> tuple[dict[uuid.UUID, list[str]], dict[uuid.UUID, dict[str, list[str]]]]:
    """(doc_id → numbers of the DRAFT batches already holding it,
    pa id → invoice id → the same, for the invoices those lines pay).

    A draft reserves nothing, so a document may sit in several (ISS-0163: it
    stays selectable, but marked); only one execution can pay it — the
    executor locks the document and refuses a second payment."""
    if not doc_ids:
        return {}, {}
    rows = (await db.execute(
        select(PaymentBatchLine.doc_id, PaymentBatchLine.invoice_ids, PaymentBatch.batch_number)
        .join(PaymentBatch, PaymentBatch.id == PaymentBatchLine.batch_id)
        .where(PaymentBatch.status == DRAFT, PaymentBatchLine.doc_id.in_(doc_ids))
        .order_by(PaymentBatch.batch_number)
    )).all()
    all_invoices = {p.id: p.invoice_ids or [] for p in pas}
    by_doc: dict[uuid.UUID, list[str]] = {}
    by_invoice: dict[uuid.UUID, dict[str, list[str]]] = {}
    for doc_id, invoice_ids, number in rows:
        by_doc.setdefault(doc_id, []).append(number)
        for inv in invoice_ids or all_invoices.get(doc_id, []):
            by_invoice.setdefault(doc_id, {}).setdefault(str(inv), []).append(number)
    return by_doc, by_invoice


async def create_batch(db: AsyncSession, *,
                       docs: list[tuple[str, uuid.UUID, list[uuid.UUID] | None]],
                       payment_method: str, batch_date: date,
                       created_by: uuid.UUID) -> PaymentBatch:
    """Snapshot selected approved PAs and/or claims into a draft batch. A doc's
    third element picks some of a PA's invoices; None takes all that is unpaid.
    Raises ValueError on empty / not-found / non-approved / mixed-currency
    selection, or on invoices the PA cannot be paid by."""
    if not docs:
        raise ValueError("Select at least one document to pay")
    pa_ids = [d_id for kind, d_id, _ in docs if kind in ("pa", "pa_dir")]
    claim_ids = [d_id for kind, d_id, _ in docs if kind == "expense_claim"]
    requested = {d_id: inv for _, d_id, inv in docs}

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
    not_payable = [c.claim_number for c in claims
                   if c.claim_type in payment_execute.NON_PAYABLE_CLAIM_TYPES]
    if not_payable:
        raise ValueError(f"Travel Applications are not payable: {not_payable}")
    currencies = {p.currency for p in pas} | {c.currency for c in claims}
    if len(currencies) > 1:
        raise ValueError(f"A batch must be single-currency; got {sorted(currencies)}")

    paid = await payment_execute.pa_split_payments(db, pa_ids)
    scopes = {p.id: await payment_execute.pa_payment_scope(
        db, p, requested.get(p.id), paid.get(p.id)) for p in pas}
    total = (sum((gross for gross, _, _ in scopes.values()), Decimal("0"))
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
            doc_kind="pa_dir" if p.is_direct else "pa",
            doc_id=p.id, doc_number=p.pa_number,
            amount=scopes[p.id][0], invoice_ids=scopes[p.id][1],
        ))
    for c in claims:
        db.add(PaymentBatchLine(
            batch_id=batch.id, doc_kind="expense_claim",
            doc_id=c.id, doc_number=c.claim_number, amount=c.total_amount,
        ))
    await db.flush()
    return batch


async def cancel_batch(db: AsyncSession, batch: PaymentBatch) -> PaymentBatch:
    """Cancel a draft batch (ISS-0163). Its lines stay as history; nothing
    else changes, because a draft reserves nothing — its documents are still
    approved and simply stop showing the "in draft" mark."""
    if batch.status != DRAFT:
        raise ValueError(f"Only a draft batch can be cancelled (this one is {batch.status})")
    batch.status = CANCELLED
    await db.flush()
    return batch


async def execute_batch(db: AsyncSession, batch: PaymentBatch, user: dict,
                        bearer_token: str | None,
                        bank_account_id: uuid.UUID | None = None,
                        credit_ids_by_doc: dict[uuid.UUID, list[uuid.UUID]] | None = None,
                        ) -> PaymentBatch:
    """Run every line through the unified executor under one batch tag. A line
    failure is isolated (savepoint rollback) and recorded; the rest proceed.
    bank_account_id (chosen at execute) funds every line and is recorded on the
    batch + each payment_record. credit_ids_by_doc is keyed by doc_id: a
    document absent from the map keeps the automatic FIFO default (None),
    while an explicit empty list opts that line out of netting."""
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
                        credit_ids=(credit_ids_by_doc or {}).get(ln.doc_id),
                        invoice_ids=ln.invoice_ids, expected_amount=ln.amount,
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
