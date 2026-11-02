"""AP invoice CRUD — finance-owned, upserted from EPMS/OA by (source, source_invoice_id)."""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import delete as sa_delete, func, literal_column, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._numbering import next_number
from app.models.ap_invoice import ApInvoice, ApInvoiceTaxLine, VOID

_ZERO = Decimal("0")


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


async def _next_number(db: AsyncSession) -> str:
    # max-suffix, not count: voided-and-deleted invoices leave gaps, and a
    # count would reissue a number that is still taken (unique constraint).
    # Shared allocator also takes a per-prefix advisory lock for concurrency.
    today = date.today().strftime("%Y%m%d")
    return await next_number(db, ApInvoice.ap_invoice_number, f"AP-{today}-", width=4)


async def get_by_source(db: AsyncSession, *, source: str,
                        source_invoice_id: uuid.UUID) -> ApInvoice | None:
    return (await db.execute(
        select(ApInvoice).where(ApInvoice.source == source,
                                ApInvoice.source_invoice_id == source_invoice_id)
    )).scalar_one_or_none()


async def delete_invoice(db: AsyncSession, inv: ApInvoice) -> None:
    """Hard-delete an AP invoice (tax lines cascade via FK). Only for invoices
    that never touched the GL and were never paid — callers must check."""
    await db.delete(inv)
    await db.flush()


async def void_for_deleted_source(db: AsyncSession, inv: ApInvoice) -> str:
    """The source system deleted this invoice. An AP row that never touched
    the GL and was never paid is just noise — remove it ("deleted"). A posted
    one stays for the audit trail, voided, with its accrual reversed out of the
    GL ("voided"). The one rule for POST /ap/invoices status=void and for
    scripts.reconcile_epms_ap."""
    from app.crud.ap_accrual import has_postings, reverse_invoice_accrual
    if not await has_postings(db, inv.id) and inv.paid_amount == Decimal("0"):
        await delete_invoice(db, inv)
        return "deleted"
    inv.status = VOID
    await reverse_invoice_accrual(db, inv.id)
    await db.flush()
    return "voided"


async def upsert(db: AsyncSession, *, source: str, source_invoice_id: uuid.UUID,
                 payload: dict, tax_lines: list[dict]) -> ApInvoice:
    """Create or update an AP invoice keyed on (source, source_invoice_id).
    payload keys: source_ref, vendor_id, vendor_name, vendor_invoice_number,
    amount, tax_amount, total_amount, currency, invoice_date, due_date, status,
    source_status, po_id, po_number. Tax lines are rebuilt each call."""
    inv = await get_by_source(db, source=source, source_invoice_id=source_invoice_id)

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


_IN_NC_SQL = r"""exists (
    select 1 from nc_ap_bill_lines l
      join nc_ap_bills b on b.id = l.bill_id
      join vendors v on v.id = ap_invoices.vendor_id
     where replace(l.invoice_no_norm, '-', '') = nullif(replace(upper(regexp_replace(
               coalesce(ap_invoices.vendor_invoice_number, ''), '\s', '', 'g')), '-', ''), '')
       and l.supplier_code = v.erp_id
       and (b.src_syscode is distinct from 17 or b.bill_no = ap_invoices.nc_bill_no))"""


# An EPMS AP on a PO imported from NC (type 1, raw material / packaging): NC
# makes that payable itself from its purchase invoice, so UniOps never pushes
# it (nc_push refuses it too). Covers the invoice's own PO and multi-PO
# allocations — the same POs nc_push.plan_bill looks at.
NC_FLOW_SQL = """ap_invoices.source = 'epms' and exists (
    select 1 from invoices i
      join purchase_orders p on p.source = 'nc'
       and (p.id = i.po_id or p.id in (select al.po_id from invoice_po_allocations al
                                        where al.invoice_id = i.id))
     where i.id = ap_invoices.source_invoice_id)"""


# Sort key for the NC AP Doc column: our pushed bill, else the first mirror bill
# that _IN_NC_SQL would match — so hand-entered bills sort with the rest.
NC_DOC_SORT = literal_column(r"""coalesce(ap_invoices.nc_bill_no, (
    select min(b.bill_no) from nc_ap_bill_lines l
      join nc_ap_bills b on b.id = l.bill_id
      join vendors v on v.id = ap_invoices.vendor_id
     where replace(l.invoice_no_norm, '-', '') = nullif(replace(upper(regexp_replace(
               coalesce(ap_invoices.vendor_invoice_number, ''), '\s', '', 'g')), '-', ''), '')
       and l.supplier_code = v.erp_id
       and (b.src_syscode is distinct from 17 or b.bill_no = ap_invoices.nc_bill_no)))""")


async def list_invoices(db: AsyncSession, *, source: str | None = None,
                        vendor_id: uuid.UUID | None = None, status: str | None = None,
                        q: str | None = None, exported: bool | None = None,
                        in_nc: bool | None = None,
                        limit: int = 50, offset: int = 0,
                        order: list | None = None) -> tuple[int, list[ApInvoice]]:
    """Paginated envelope (total, page). `q` matches vendor_invoice_number /
    vendor_name; `exported` filters on nc_exported_at presence. `order` is the
    caller's whitelisted ORDER BY (app.core.list_sort); None = newest first."""
    base = select(ApInvoice)
    if source:
        base = base.where(ApInvoice.source == source)
    if vendor_id:
        base = base.where(ApInvoice.vendor_id == vendor_id)
    if status:
        base = base.where(ApInvoice.status == status)
    if q:
        like = f"%{q}%"
        base = base.where(or_(ApInvoice.vendor_invoice_number.ilike(like),
                              ApInvoice.vendor_name.ilike(like)))
    if exported is True:
        base = base.where(ApInvoice.nc_exported_at.is_not(None))
    elif exported is False:
        base = base.where(ApInvoice.nc_exported_at.is_(None))
    if in_nc is not None:
        # "Hide in NC": an AP is in NC when UniOps pushed it, or the NC AP
        # mirror holds its invoice number for its supplier (any approval
        # state; our own replaced bills excluded) — same match as the list's
        # NC AP Doc column (services/nc_push.nc_docs_for). An NC-flow AP is
        # hidden too: NC books it itself, there is nothing to push.
        has = or_(ApInvoice.nc_bill_no.is_not(None), text(_IN_NC_SQL),
                  text(f"({NC_FLOW_SQL})"))
        base = base.where(has if in_nc else ~has)
    total = (await db.execute(
        select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = list((await db.execute(
        base.order_by(*(order or (ApInvoice.invoice_date.desc(),
                                  ApInvoice.ap_invoice_number.desc())))
        .offset(offset).limit(limit))).scalars().all())
    return total, rows


async def nc_flow_ids(db: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    return set((await db.execute(select(ApInvoice.id).where(
        ApInvoice.id.in_(ids), text(f"({NC_FLOW_SQL})")))).scalars().all())


async def gl_codes(db: AsyncSession, rows: list[ApInvoice]) -> dict[uuid.UUID, str]:
    """AP id -> budget account code(s), from the same source the NC push takes
    its revenue/expense item from (services/nc_push._summary_and_dims):
    PO route = the PO(s) — own PO plus multi-PO allocations, distinct,
    comma-joined; courier = the fixed freight item; Direct / OA = the Direct
    PA's budget account; agreement = the agreement's. Nothing known -> absent."""
    from app.models.mirrors import (ExpenseInvoice, Invoice, InvoicePoAllocation,
                                    PurchaseAgreement, PurchaseOrder)
    from app.models.pa import PaymentApplication
    from app.services.nc_push import FREIGHT_REVEXP, _leaf_budget
    by_ap: dict[uuid.UUID, set[str]] = {}

    def put(ap_id, code):
        c = _leaf_budget(code)
        if c:
            by_ap.setdefault(ap_id, set()).add(c)

    # OA: expense invoice -> its Direct PA.
    oa = {r.source_invoice_id: r.id for r in rows if r.source == "oa"}
    if oa:
        pa_of = dict((await db.execute(
            select(ExpenseInvoice.id, ExpenseInvoice.pa_id)
            .where(ExpenseInvoice.id.in_(list(oa)), ExpenseInvoice.pa_id.is_not(None)))).all())
        code = dict((await db.execute(
            select(PaymentApplication.id, PaymentApplication.budget_account_code)
            .where(PaymentApplication.id.in_(set(pa_of.values()))))).all()) if pa_of else {}
        for ei, pa in pa_of.items():
            put(oa[ei], code.get(pa))

    src = {r.source_invoice_id: r.id for r in rows if r.source == "epms"}
    if not src:
        return {ap: ", ".join(sorted(cs)) for ap, cs in by_ap.items()}
    invs = list((await db.execute(
        select(Invoice.id, Invoice.match_route, Invoice.agreement_id)
        .where(Invoice.id.in_(list(src))))).all())
    po_side = [i for i, route, _ in invs if (route or "po") not in ("freight", "direct", "agreement")]
    for i, route, _ in invs:
        if route == "freight":
            put(src[i], FREIGHT_REVEXP)
    for i, route, _ in invs:
        if route == "direct":
            pa = (await db.execute(select(PaymentApplication.budget_account_code).where(
                PaymentApplication.invoice_ids.contains([str(i)]),
                PaymentApplication.status != "cancelled").limit(1))).scalars().first()
            put(src[i], pa)
    agr_of = {i: a for i, route, a in invs if route == "agreement" and a}
    if agr_of:
        code = dict((await db.execute(
            select(PurchaseAgreement.id, PurchaseAgreement.budget_code)
            .where(PurchaseAgreement.id.in_(set(agr_of.values()))))).all())
        for i, a in agr_of.items():
            put(src[i], code.get(a))

    if po_side:
        pairs = list((await db.execute(
            select(Invoice.id, Invoice.po_id)
            .where(Invoice.id.in_(po_side), Invoice.po_id.is_not(None)))).all())
        pairs += list((await db.execute(
            select(InvoicePoAllocation.invoice_id, InvoicePoAllocation.po_id)
            .where(InvoicePoAllocation.invoice_id.in_(po_side)))).all())
        if pairs:
            code = dict((await db.execute(
                select(PurchaseOrder.id, PurchaseOrder.budget_code)
                .where(PurchaseOrder.id.in_({po for _, po in pairs})))).all())
            for inv_id, po_id in pairs:
                put(src[inv_id], code.get(po_id))
    return {ap: ", ".join(sorted(cs)) for ap, cs in by_ap.items()}


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
